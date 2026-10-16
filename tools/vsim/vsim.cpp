// vsim: the board design (Cu + Io) compiled by Verilator, with the Io front panel drawn in an SDL
// window. It runs the Verilog that `alchitry build` writes (build/source/*.v, the hardware
// parameters: SLOW_BITS 20, TICK_DIV 25, 1 Mbaud) with the PLL bypassed, so one clk here is one
// 25 MHz sys_clk on the board (40 ns). All flip-flops update together (unlike the Alchitry
// simulator), and main RAM is the BRAM Verilog, not the Lucid model.
//
// Artwork and layout follow Alchitry Labs V2's board view (GPL-3.0, github.com/alchitry/
// Alchitry-Labs-V2, ui/simulation/IoBoard.kt, AlchitryBoard.kt). The SVGs are read at start-up
// from the installed Alchitry Labs jar and rasterised with ImageMagick; nothing of theirs is
// stored in this repository.
//
// Serial: usb_tx is decoded (8N1) and echoed to stdout and to a pseudo-terminal whose path is
// printed at start-up (`picocom -b 1000000 /dev/pts/N` works; the baud setting is ignored).
//
// Built and started by tools/vsim/vsim.sh, which normally builds the design with its timing 5x
// shorter (SLOW_BITS, TICK_DIV, DEBOUNCE_BITS) and passes --time-scale 5: then 5 MHz here is
// real time, and "board time" in the title is what a stopwatch on the real board would show.

#include "Vbasic_riscve_16k16k.h"
#include "verilated.h"

#include <SDL.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <termios.h>
#include <thread>
#include <unistd.h>
#include <vector>

static const double SYS_HZ = 25e6;             // the board's sys_clk: one simulated clock = 40 ns
static const int CLKS_PER_BIT = 25;            // 1 Mbaud at 25 MHz (machine UART_BAUD)
static const double BOARD_W = 65.0, BOARD_H = 45.0;   // both boards' artwork, mm

struct Options {
    std::string jar = "/usr/lib/alchitry/alchitry-labs/lib/app/Alchitry Labs V2-2.0.57.jar";
    double scale = 11.0;       // pixels per mm
    bool throttle = true;      // never run faster than the real board
    bool echo = true;          // serial bytes to stdout
    int time_scale = 1;        // the design was built with its timing this many times shorter
    uint64_t headless = 0;     // run this many clocks without a window, then print the outputs
    unsigned dips = 0;         // starting DIP switches, io_dip[23:0]
    std::string snapshot;      // save the window to this .bmp after snapshot_clocks, then quit
    uint64_t snapshot_clocks = 0;
};

// ---------------------------------------------------------------------------------------------
// Artwork: an SVG from the jar → RGBA pixels → SDL texture.

static SDL_Texture* load_svg(SDL_Renderer* r, const Options& o, const char* name, int w, int h) {
    // 96 dpi is ImageMagick's SVG unit; render at about the target size, then force it exactly.
    char cmd[1024];
    snprintf(cmd, sizeof cmd,
             "unzip -p '%s' 'boards/%s' | convert -background none -density 300 svg:- "
             "-resize %dx%d! -depth 8 RGBA:-", o.jar.c_str(), name, w, h);
    FILE* p = popen(cmd, "r");
    if (!p) { perror("popen"); exit(1); }
    std::vector<unsigned char> px((size_t)w * h * 4);
    size_t got = fread(px.data(), 1, px.size(), p);
    pclose(p);
    if (got != px.size()) {
        fprintf(stderr, "vsim: couldn't rasterise boards/%s from %s (got %zu of %zu bytes)\n",
                name, o.jar.c_str(), got, px.size());
        exit(1);
    }
    SDL_Texture* t = SDL_CreateTexture(r, SDL_PIXELFORMAT_RGBA32, SDL_TEXTUREACCESS_STATIC, w, h);
    SDL_UpdateTexture(t, nullptr, px.data(), w * 4);
    SDL_SetTextureBlendMode(t, SDL_BLENDMODE_BLEND);
    return t;
}

// ---------------------------------------------------------------------------------------------
// Serial: 8N1 decoder on usb_tx, and a pseudo-terminal to hand the bytes to.

struct UartRx {
    int state = 0;             // 0 idle, 1 checking the start bit, 2 receiving
    int count = 0, bit = 0;
    unsigned byte = 0;
    bool last = true;
    // Returns a byte when its stop bit is sampled, else -1. Like a real receiver, a start bit
    // must still be low half a bit after the falling edge: uart_tx's output register has no
    // reset, so TX is low for the first couple of clocks after power-up.
    int clock(bool line) {
        int out = -1;
        if (state == 0) {
            if (last && !line) { state = 1; count = CLKS_PER_BIT / 2; }
        } else if (--count == 0) {
            if (state == 1) {
                if (!line) { state = 2; count = CLKS_PER_BIT; bit = 0; byte = 0; }
                else state = 0;                               // a glitch, not a start bit
            } else if (bit < 8) { byte |= (unsigned)line << bit; bit++; count = CLKS_PER_BIT; }
            else { if (line) out = (int)byte; state = 0; }   // framing error: dropped
        }
        last = line;
        return out;
    }
};

static int open_pty(std::string& path) {
    int m = posix_openpt(O_RDWR | O_NOCTTY);
    if (m < 0 || grantpt(m) || unlockpt(m)) return -1;
    path = ptsname(m);
    int s = open(path.c_str(), O_RDWR | O_NOCTTY);   // kept open, so writes don't fail with no reader
    if (s >= 0) {
        termios t;
        tcgetattr(s, &t);
        cfmakeraw(&t);
        tcsetattr(s, TCSANOW, &t);
    }
    fcntl(m, F_SETFL, O_NONBLOCK);                    // a full buffer drops bytes rather than stalling
    return m;
}

// ---------------------------------------------------------------------------------------------
// Brightness: each output's on-time over a frame, counted in runs (only when a value changes).

struct Accum {
    unsigned last = 0;
    uint64_t run = 0;
    std::vector<uint64_t> on;
    explicit Accum(int bits) : on(bits, 0) {}
    void flush() {
        for (size_t b = 0; b < on.size(); b++) if (last >> b & 1) on[b] += run;
        run = 0;
    }
    inline void sample(unsigned v) {
        if (v != last) { flush(); last = v; }
        run++;
    }
};

// ---------------------------------------------------------------------------------------------

struct Rect { double x, y, w, h; };

int main(int argc, char** argv) {
    Options o;
    Verilated::commandArgs(argc, argv);
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--scale" && i + 1 < argc) o.scale = atof(argv[++i]);
        else if (a == "--jar" && i + 1 < argc) o.jar = argv[++i];
        else if (a == "--unthrottled") o.throttle = false;
        else if (a == "--quiet") o.echo = false;
        else if (a == "--time-scale" && i + 1 < argc) o.time_scale = atoi(argv[++i]);
        else if (a == "--headless" && i + 1 < argc) o.headless = strtoull(argv[++i], nullptr, 0);
        else if (a == "--dips" && i + 1 < argc) o.dips = (unsigned)strtoul(argv[++i], nullptr, 16);
        else if (a == "--snapshot" && i + 2 < argc) {
            o.snapshot = argv[++i];
            o.snapshot_clocks = strtoull(argv[++i], nullptr, 0);
        }
        else if (a == "--help") {
            printf("vsim [--scale PX_PER_MM] [--jar ALCHITRY_JAR] [--unthrottled] [--quiet]\n"
                   "     [--dips HEX] [--headless CLOCKS] [--snapshot FILE.bmp CLOCKS]\n");
            return 0;
        }
    }

    // Clocks per second of board time: 25 MHz, or less when the design's timing is scaled down
    const double REAL_HZ = SYS_HZ / (o.time_scale > 0 ? o.time_scale : 1);

    if (o.headless) {
        // No window: the given DIPs, a power-on reset, then CLOCKS clocks at full speed.
        Vbasic_riscve_16k16k* top = new Vbasic_riscve_16k16k;
        top->io_button = 0;
        top->io_dip = o.dips;
        UartRx rx;
        uint64_t cpu_clocks = 0;   // Cu LED 4 toggles once per enabled cpu clock
        unsigned led4 = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (uint64_t c = 0; c < o.headless; c++) {
            top->rst_n = c >= 16;
            top->clk = 1;
            top->eval();
            top->clk = 0;
            top->eval();
            int b = rx.clock(top->usb_tx);
            if (b >= 0 && o.echo) { fputc(b, stdout); fflush(stdout); }
            unsigned l = top->led >> 4 & 1;
            cpu_clocks += l != led4;
            led4 = l;
        }
        double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        fprintf(stderr, "\nvsim: %llu clocks (%.3f s of board time) in %.2f s: %.2f MHz, %.0f%% of real time\n",
                (unsigned long long)o.headless, o.headless / REAL_HZ, dt, o.headless / dt / 1e6,
                100 * o.headless / dt / REAL_HZ);
        fprintf(stderr, "vsim: %llu cpu clocks\n", (unsigned long long)cpu_clocks);
        fprintf(stderr, "vsim: led %02x  io_led %06x  io_select %x  io_segment %02x\n",
                (unsigned)top->led, (unsigned)top->io_led, (unsigned)top->io_select, (unsigned)top->io_segment);
        top->final();
        delete top;
        return 0;
    }

    std::string pty_path;
    int pty = open_pty(pty_path);
    if (pty >= 0) fprintf(stderr, "vsim: serial on %s\n", pty_path.c_str());

    if (SDL_Init(SDL_INIT_VIDEO)) { fprintf(stderr, "SDL: %s\n", SDL_GetError()); return 1; }
    const double s = o.scale;
    const int bw = (int)lround(BOARD_W * s), bh = (int)lround(BOARD_H * s);
    SDL_Window* win = SDL_CreateWindow("vsim", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                       2 * bw, bh, 0);
    SDL_Renderer* ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    SDL_SetRenderDrawBlendMode(ren, SDL_BLENDMODE_BLEND);

    SDL_Texture* cu_tex = load_svg(ren, o, "cu.svg", bw, bh);
    SDL_Texture* io_tex = load_svg(ren, o, "io.svg", bw, bh);
    SDL_SetTextureColorMod(cu_tex, 217, 217, 217);      // the IDE darkens the boards to 85%
    SDL_SetTextureColorMod(io_tex, 217, 217, 217);
    SDL_Texture* seg_tex[8];
    const int dw = (int)lround(7.5 * s), dh = (int)lround(10.0 * s);
    for (int k = 0; k < 8; k++) {
        char n[16];
        snprintf(n, sizeof n, "io_%d.svg", k);
        seg_tex[k] = load_svg(ren, o, n, dw, dh);
    }

    // Layout, mm, from IoBoard.kt and AlchitryBoard.kt. The Cu is on the left, the Io on the right.
    const double IOX = BOARD_W;
    Rect cu_led[8], cu_done = {2.089, 26.866, 1.820, 1.268}, cu_reset = {59.080, 30.125, 4.838, 4.750};
    for (int i = 0; i < 8; i++) cu_led[i] = {61.089, 10.366 + 2.0 * i, 1.820, 1.268};
    Rect io_led[24], dip[24], digit[4], button[5];
    {
        double x = 54.366;
        for (int j = 0; j < 3; j++) {
            for (int i = 0; i < 8; i++) { io_led[j * 8 + i] = {IOX + x, 24.841, 1.268, 1.820}; x -= 2.0; }
            x += (36.371 - 40.366) + 2.0;
        }
        x = 52.150;
        for (int j = 0; j < 3; j++) {
            for (int i = 0; i < 8; i++) { dip[j * 8 + i] = {IOX + x, 33.900, 0.8, 3.2}; x -= 1.3; }
            x += -8.9 + 1.3;
        }
        for (int i = 0; i < 4; i++) digit[i] = {IOX + 29.0 - 7.75 * i, 10.5, 7.5, 10.0};
        // io_button order: 0 up, 1 centre, 2 down, 3 left, 4 right (vertical ones are rotated)
        button[0] = {IOX + 46.125, 3.581, 4.750, 4.838};
        button[1] = {IOX + 46.125, 10.081, 4.750, 4.838};
        button[2] = {IOX + 46.125, 16.581, 4.750, 4.838};
        button[3] = {IOX + 39.581, 10.125, 4.838, 4.750};
        button[4] = {IOX + 52.581, 10.125, 4.838, 4.750};
    }
    auto px = [&](const Rect& r) {
        SDL_Rect q = {(int)lround(r.x * s), (int)lround(r.y * s), (int)lround(r.w * s), (int)lround(r.h * s)};
        return q;
    };
    auto inside = [&](const Rect& r, int mx, int my) {
        return mx >= r.x * s && mx < (r.x + r.w) * s && my >= r.y * s && my < (r.y + r.h) * s;
    };

    // Inputs
    unsigned dips = o.dips;
    bool buttons[5] = {};
    bool reset_held = false;
    int mouse_button = -1;     // which push button the mouse is holding (-1 none, 5 = Cu reset)

    Vbasic_riscve_16k16k* top = new Vbasic_riscve_16k16k;
    top->clk = 0;
    top->rst_n = 0;
    top->io_button = 0;
    top->io_dip = 0;
    top->eval();

    Accum leds(32);            // io_led [23:0], Cu led [31:24]
    Accum segs(32);            // digit d segment k at bit d*8+k, lit = select low and segment low
    UartRx rx;
    float led_level[32] = {}, seg_level[32] = {};

    uint64_t clocks = 0, frame_clocks = 0;
    auto t0 = std::chrono::steady_clock::now();
    auto last_title = t0;
    uint64_t title_clocks = 0;
    int power_on = 16;         // hold rst_n low for the first clocks, like the reset button

    bool running = true;
    while (running) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) running = false;
            else if (e.type == SDL_KEYDOWN && (e.key.keysym.sym == SDLK_ESCAPE || e.key.keysym.sym == SDLK_q))
                running = false;
            else if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT) {
                int mx = e.button.x, my = e.button.y;
                for (int i = 0; i < 24; i++) if (inside(dip[i], mx, my)) dips ^= 1u << i;
                for (int i = 0; i < 5; i++) if (inside(button[i], mx, my)) { buttons[i] = true; mouse_button = i; }
                if (inside(cu_reset, mx, my)) { reset_held = true; mouse_button = 5; }
            } else if (e.type == SDL_MOUSEBUTTONUP && e.button.button == SDL_BUTTON_LEFT) {
                if (mouse_button == 5) reset_held = false;
                else if (mouse_button >= 0) buttons[mouse_button] = false;
                mouse_button = -1;
            }
        }

        unsigned btn = 0;
        for (int i = 0; i < 5; i++) btn |= (unsigned)buttons[i] << i;
        top->io_button = btn;
        top->io_dip = dips;

        // Run clocks for most of a 60 Hz frame, in chunks, never ahead of the real board.
        auto frame_start = std::chrono::steady_clock::now();
        const auto budget = std::chrono::microseconds(14000);
        while (std::chrono::steady_clock::now() - frame_start < budget) {
            if (o.throttle) {
                double real = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
                if (clocks > real * REAL_HZ) break;
            }
            for (int n = 0; n < 4096; n++) {
                top->rst_n = !(reset_held || power_on > 0);
                if (power_on > 0) power_on--;
                top->clk = 1;
                top->eval();
                top->clk = 0;
                top->eval();
                leds.sample((top->io_led & 0xFFFFFF) | (unsigned)top->led << 24);
                unsigned lit = 0;
                for (int d = 0; d < 4; d++)
                    if (!(top->io_select >> d & 1)) lit |= (~(unsigned)top->io_segment & 0xFF) << (d * 8);
                segs.sample(lit);
                int b = rx.clock(top->usb_tx);
                if (b >= 0) {
                    unsigned char c = (unsigned char)b;
                    if (o.echo) { fputc(c, stdout); fflush(stdout); }
                    if (pty >= 0 && write(pty, &c, 1) < 0) { /* no room: dropped */ }
                }
            }
            clocks += 4096;
            frame_clocks += 4096;
        }

        if (frame_clocks) {
            leds.flush();
            segs.flush();
            for (int b = 0; b < 32; b++) {
                led_level[b] = powf((float)leds.on[b] / frame_clocks, 0.45f);
                // IoSimulator raises segments to 0.3, then SegmentDigit to 0.45
                seg_level[b] = powf(powf((float)segs.on[b] / frame_clocks, 0.3f), 0.45f);
                leds.on[b] = 0;
                segs.on[b] = 0;
            }
            frame_clocks = 0;
        }

        // Draw
        SDL_SetRenderDrawColor(ren, 30, 30, 30, 255);
        SDL_RenderClear(ren);
        SDL_Rect cu_dst = {0, 0, bw, bh}, io_dst = {bw, 0, bw, bh};
        SDL_RenderCopy(ren, cu_tex, nullptr, &cu_dst);
        SDL_RenderCopy(ren, io_tex, nullptr, &io_dst);

        auto draw_led = [&](const Rect& r, float level) {
            if (level <= 0.003f) return;
            const double g = 0.2;                       // glow, mm (the IDE's blur)
            Rect halo = {r.x - g, r.y - g, r.w + 2 * g, r.h + 2 * g};
            SDL_Rect q = px(halo);
            SDL_SetRenderDrawColor(ren, 0x5F, 0xFF, 0x00, (Uint8)(level * 90));
            SDL_RenderFillRect(ren, &q);
            q = px(r);
            SDL_SetRenderDrawColor(ren, 0x5F, 0xFF, 0x00, (Uint8)(level * 255));
            SDL_RenderFillRect(ren, &q);
        };
        for (int i = 0; i < 24; i++) draw_led(io_led[i], led_level[i]);
        for (int i = 0; i < 8; i++) draw_led(cu_led[i], led_level[24 + i]);
        draw_led(cu_done, 1.0f);

        for (int d = 0; d < 4; d++) {
            SDL_Rect q = px(digit[d]);
            for (int k = 0; k < 8; k++) {
                float v = seg_level[d * 8 + k];
                if (v <= 0.003f) continue;
                SDL_SetTextureAlphaMod(seg_tex[k], (Uint8)(v * 255));
                SDL_RenderCopy(ren, seg_tex[k], nullptr, &q);
            }
        }

        for (int i = 0; i < 24; i++) {
            const Rect& r = dip[i];
            Rect knob = {r.x + 0.1, r.y + ((dips >> i & 1) ? 0.675 : 1.725), 0.6, 0.6};
            SDL_Rect q = px(knob);
            SDL_SetRenderDrawColor(ren, 255, 255, 255, 255);
            SDL_RenderFillRect(ren, &q);
        }

        auto draw_pressed = [&](const Rect& r) {
            // the IDE's pressed button: a filled circle, radius 0.243 of the width
            double cx = (r.x + r.w / 2) * s, cy = (r.y + r.h / 2) * s, rad = 0.243386 * r.w * s;
            SDL_SetRenderDrawColor(ren, 0xAB, 0x6E, 0x1D, 255);
            for (int dy = -(int)rad; dy <= (int)rad; dy++) {
                int dx = (int)sqrt(rad * rad - dy * dy);
                SDL_RenderDrawLine(ren, (int)(cx - dx), (int)(cy + dy), (int)(cx + dx), (int)(cy + dy));
            }
        };
        for (int i = 0; i < 5; i++) if (buttons[i]) draw_pressed(button[i]);
        if (reset_held) draw_pressed(cu_reset);

        if (!o.snapshot.empty() && clocks >= o.snapshot_clocks) {
            SDL_Surface* shot = SDL_CreateRGBSurfaceWithFormat(0, 2 * bw, bh, 32, SDL_PIXELFORMAT_ARGB8888);
            SDL_RenderReadPixels(ren, nullptr, SDL_PIXELFORMAT_ARGB8888, shot->pixels, shot->pitch);
            SDL_SaveBMP(shot, o.snapshot.c_str());
            SDL_FreeSurface(shot);
            fprintf(stderr, "vsim: saved %s at %llu clocks\n", o.snapshot.c_str(), (unsigned long long)clocks);
            running = false;
        }
        SDL_RenderPresent(ren);

        auto now = std::chrono::steady_clock::now();
        double dt = std::chrono::duration<double>(now - last_title).count();
        if (dt >= 0.5) {
            double hz = (clocks - title_clocks) / dt;
            double t = clocks / REAL_HZ;
            char title[160];
            snprintf(title, sizeof title, "vsim  %.2f MHz  %.0f%% of real time  board time %.1f s%s",
                     hz / 1e6, 100 * hz / REAL_HZ, t, o.throttle ? "" : "  unthrottled");
            SDL_SetWindowTitle(win, title);
            last_title = now;
            title_clocks = clocks;
        }
    }

    top->final();
    delete top;
    SDL_Quit();
    return 0;
}
