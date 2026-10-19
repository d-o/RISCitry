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
// printed at start-up (`picocom -b 1000000 /dev/pts/N` works; the baud setting is ignored). What
// is typed into the pseudo-terminal is sent to usb_rx (8N1, 25 clocks a bit), and --send TIME TEXT
// queues text at an exact clock (also headless).
//
// Built and started by tools/vsim/vsim.sh, which normally builds the design with its timing 5x
// shorter (SLOW_BITS, TICK_DIV, DEBOUNCE_BITS) and passes --time-scale 5: then 5 MHz here is
// real time, and "board time" in the title is what a stopwatch on the real board would show.

#include "Vbasic_riscve_16k16k.h"
#include "verilated.h"

#include <SDL.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
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
    double scale = 0;          // pixels per mm (default 16 stacked, 11 side by side)
    bool side_by_side = false; // the Cu beside the Io instead of under it
    bool throttle = true;      // never run faster than the real board
    bool echo = true;          // serial bytes to stdout
    int time_scale = 1;        // the design was built with its timing this many times shorter
    std::string headless;      // run this long without a window, then print the outputs
    unsigned dips = 0;         // starting DIP switches, io_dip[23:0]
    std::string snapshot;      // save the window to this image after snapshot_at, then quit
    std::string snapshot_at;
    std::string record;        // video of the first record_for, through ffmpeg
    std::string record_for;
    int fps = 30;              // frames per second of board time (snapshot and record)
    std::vector<std::pair<std::string, std::string>> at;   // --at TIME ACTION, in order given
    std::vector<std::pair<std::string, std::string>> send; // --send TIME TEXT, in order given
    std::string trace;         // --trace FILE LEDS START LEN: a VCD of these io_led bits (with --headless)
    std::string trace_leds, trace_start, trace_len;
};

// A duration: "2.5s" is seconds of board time, a plain number is clocks.
static uint64_t parse_time(const std::string& t, double real_hz) {
    if (!t.empty() && t.back() == 's') return (uint64_t)(atof(t.c_str()) * real_hz);
    return strtoull(t.c_str(), nullptr, 0);
}

// ---------------------------------------------------------------------------------------------
// Artwork: an SVG from the jar → RGBA pixels → SDL texture.

static SDL_Texture* load_svg(SDL_Renderer* r, const Options& o, const char* name, int w, int h) {
    // Render at 300 dpi, then force the exact size. The perl drops io.svg's path1, a blurred
    // shadow along the cut-out on the Io's right edge: ImageMagick ignores the blur filter and
    // fills the shape black, which hides the Cu's LEDs and reset button underneath.
    char cmd[1024];
    snprintf(cmd, sizeof cmd,
             "unzip -p '%s' 'boards/%s' | perl -0pe 's/<path\\s[^>]*id=\"path1\"[^>]*\\/>//s' | "
             "convert -background none -density 300 svg:- -resize %dx%d! -depth 8 RGBA:-",
             o.jar.c_str(), name, w, h);
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

// The other direction: bytes queued here go out on usb_rx as 8N1 frames, 25 clocks a bit.
struct UartTx {
    std::deque<unsigned char> queue;
    unsigned frame = 0;        // the bits still to send, LSB first (start, data, stop)
    int bits = 0, count = 0;
    bool line = true;          // idle high
    void clock() {
        if (count > 0) { count--; return; }               // hold the current bit
        if (bits == 0) {
            if (queue.empty()) { line = true; return; }
            frame = (1u << 9) | ((unsigned)queue.front() << 1);    // stop bit, data, start bit (0)
            queue.pop_front();
            bits = 10;
        }
        line = frame & 1;
        frame >>= 1;
        bits--;
        count = CLKS_PER_BIT - 1;
    }
};

// \n, \r, \t, \\ and \xHH in --send text.
static std::string unescape(const std::string& t) {
    std::string out;
    for (size_t i = 0; i < t.size(); i++) {
        if (t[i] != '\\' || i + 1 >= t.size()) { out += t[i]; continue; }
        char c = t[++i];
        if (c == 'n') out += '\n';
        else if (c == 'r') out += '\r';
        else if (c == 't') out += '\t';
        else if (c == 'x' && i + 2 < t.size()) { out += (char)strtoul(t.substr(i + 1, 2).c_str(), nullptr, 16); i += 2; }
        else out += c;
    }
    return out;
}

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

// Scripted inputs (--at TIME ACTION): button clicks, the Cu reset, DIP changes, applied at exact
// clocks, in headless runs, recordings and the window alike (the mouse still works on top).
struct Script {
    struct Ev { uint64_t clock; int kind; unsigned arg; };   // kind: 0 buttons down mask, 1 up mask,
    std::vector<Ev> evs;                                      // 2 dips = arg, 3 reset down, 4 reset up
    size_t next = 0;
    unsigned buttons = 0;      // io_button bits held by the script
    bool reset = false;
    unsigned dips;
    explicit Script(unsigned d) : dips(d) {}
    // ACTION: up, down, left, right, centre or reset (a click, held 100 ms of board time, or
    // NAME/MS for MS ms), or dips=HEX.
    bool add(uint64_t clock, const std::string& action, double real_hz) {
        if (action.rfind("dips=", 0) == 0) {
            evs.push_back({clock, 2, (unsigned)strtoul(action.c_str() + 5, nullptr, 16)});
            return true;
        }
        std::string name = action;
        double ms = 100;
        size_t slash = action.find('/');
        if (slash != std::string::npos) { name = action.substr(0, slash); ms = atof(action.c_str() + slash + 1); }
        const uint64_t hold = (uint64_t)(ms / 1000 * real_hz);
        static const char* names[5] = {"up", "centre", "down", "left", "right"};   // io_button order
        for (int i = 0; i < 5; i++)
            if (name == names[i]) {
                evs.push_back({clock, 0, 1u << i});
                evs.push_back({clock + hold, 1, 1u << i});
                return true;
            }
        if (name == "reset") {
            evs.push_back({clock, 3, 0});
            evs.push_back({clock + hold, 4, 0});
            return true;
        }
        return false;
    }
    void sort() {
        std::stable_sort(evs.begin(), evs.end(), [](const Ev& a, const Ev& b) { return a.clock < b.clock; });
    }
    inline void run_to(uint64_t clock) {
        while (next < evs.size() && evs[next].clock <= clock) {
            const Ev& e = evs[next++];
            switch (e.kind) {
                case 0: buttons |= e.arg; break;
                case 1: buttons &= ~e.arg; break;
                case 2: dips = e.arg; break;
                case 3: reset = true; break;
                case 4: reset = false; break;
            }
        }
    }
};

struct Rect { double x, y, w, h; };

int main(int argc, char** argv) {
    Options o;
    Verilated::commandArgs(argc, argv);
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--scale" && i + 1 < argc) o.scale = atof(argv[++i]);
        else if (a == "--jar" && i + 1 < argc) o.jar = argv[++i];
        else if (a == "--unthrottled") o.throttle = false;
        else if (a == "--side-by-side") o.side_by_side = true;
        else if (a == "--quiet") o.echo = false;
        else if (a == "--time-scale" && i + 1 < argc) o.time_scale = atoi(argv[++i]);
        else if (a == "--headless" && i + 1 < argc) o.headless = argv[++i];
        else if (a == "--dips" && i + 1 < argc) o.dips = (unsigned)strtoul(argv[++i], nullptr, 16);
        else if (a == "--snapshot" && i + 2 < argc) { o.snapshot = argv[++i]; o.snapshot_at = argv[++i]; }
        else if (a == "--record" && i + 2 < argc) { o.record = argv[++i]; o.record_for = argv[++i]; }
        else if (a == "--fps" && i + 1 < argc) o.fps = atoi(argv[++i]);
        else if (a == "--at" && i + 2 < argc) { o.at.push_back({argv[i + 1], argv[i + 2]}); i += 2; }
        else if (a == "--trace" && i + 4 < argc) { o.trace = argv[++i]; o.trace_leds = argv[++i]; o.trace_start = argv[++i]; o.trace_len = argv[++i]; }
        else if (a == "--send" && i + 2 < argc) { o.send.push_back({argv[i + 1], argv[i + 2]}); i += 2; }
        else if (a == "--help") {
            printf("vsim [--scale PX_PER_MM] [--side-by-side] [--dips HEX] [--quiet] [--unthrottled]\n"
                   "     [--headless TIME] [--snapshot FILE TIME] [--record FILE TIME [--fps N]]\n"
                   "     [--at TIME ACTION]... [--send TIME TEXT]... [--jar ALCHITRY_JAR]\n"
                   "TIME: 2.5s = seconds of board time, a plain number = clocks.\n"
                   "--snapshot: any image type ImageMagick knows (.png, .bmp ...). --record: .gif\n"
                   "  (palette for the web) or anything ffmpeg writes (.mp4: H.264). Both run\n"
                   "  without a window, as fast as they can, frames timed in board time, no echo.\n"
                   "--at TIME ACTION (repeatable): ACTION up, down, left, right, centre or reset is a\n"
                   "  click held 100 ms (NAME/MS for MS ms); dips=HEX sets the switches.\n"
                   "--trace FILE LEDS START LEN (with --headless): the raw 1-bit waveform of io_led bits LEDS\n"
                   "  (comma list, LED i = io_led[i/8][i%8], e.g. 0,1,2) from START for LEN, one clock per\n"
                   "  VCD tick (40 ns), to FILE (open in GTKWave), plus a summary on stderr. The window only\n"
                   "  shows each LED's average brightness, so PWM and sigma-delta look the same there:\n"
                   "  the difference is in this waveform.\n"
                   "--send TIME TEXT (repeatable): TEXT goes into the serial input at that clock (\\n, \\xHH\n"
                   "  escapes); in a window, what is typed into the serial pty goes in too.\n");
            return 0;
        } else {
            fprintf(stderr, "vsim: unknown option %s (--help)\n", a.c_str());
            return 1;
        }
    }

    // Clocks per second of board time: 25 MHz, or less when the design's timing is scaled down
    const double REAL_HZ = SYS_HZ / (o.time_scale > 0 ? o.time_scale : 1);
    if (!o.trace.empty() && o.headless.empty()) {
        fprintf(stderr, "vsim: --trace needs --headless TIME (at least START + LEN)\n");
        return 1;
    }
    const uint64_t headless_clocks = o.headless.empty() ? 0 : parse_time(o.headless, REAL_HZ);
    // Offline: a snapshot or a recording. No window, no throttle, no echo, and every frame is
    // exactly 1/fps of board time, so the pictures don't depend on how fast this machine is.
    const bool offline = !o.snapshot.empty() || !o.record.empty();
    const uint64_t snapshot_clocks = o.snapshot.empty() ? 0 : parse_time(o.snapshot_at, REAL_HZ);
    const uint64_t record_clocks = o.record.empty() ? 0 : parse_time(o.record_for, REAL_HZ);
    const uint64_t frame_len = (uint64_t)(REAL_HZ / (o.fps > 0 ? o.fps : 30));
    if (offline) {
        o.throttle = false;
        o.echo = false;
        setenv("SDL_VIDEODRIVER", "offscreen", 0);
    }

    Script script(o.dips);
    for (auto& [t, action] : o.at)
        if (!script.add(parse_time(t, REAL_HZ), action, REAL_HZ)) {
            fprintf(stderr, "vsim: --at %s %s: unknown action\n", t.c_str(), action.c_str());
            return 1;
        }
    script.sort();

    UartTx serial_in;
    std::vector<std::pair<uint64_t, std::string>> sends;
    for (auto& [t, text] : o.send) sends.push_back({parse_time(t, REAL_HZ), unescape(text)});
    std::stable_sort(sends.begin(), sends.end(), [](auto& a, auto& b) { return a.first < b.first; });
    size_t next_send = 0;
    auto feed = [&](uint64_t clock) {          // queue what is due, then step the serial line
        while (next_send < sends.size() && sends[next_send].first <= clock) {
            for (unsigned char ch : sends[next_send].second) serial_in.queue.push_back(ch);
            next_send++;
        }
        serial_in.clock();
    };

    if (headless_clocks) {
        // No window: the given DIPs, a power-on reset, then CLOCKS clocks at full speed.
        Vbasic_riscve_16k16k* top = new Vbasic_riscve_16k16k;
        top->io_button = 0;
        top->io_dip = o.dips;
        top->usb_rx = 1;
        UartRx rx;
        uint64_t cpu_clocks = 0;   // Cu LED 4 toggles once per enabled cpu clock
        unsigned led4 = 0;
        // --trace: a VCD of some io_led bits over [trace_start, trace_start + trace_len)
        std::vector<int> trace_leds;
        FILE* trace = nullptr;
        const uint64_t trace_start = o.trace.empty() ? 0 : parse_time(o.trace_start, REAL_HZ);
        const uint64_t trace_end = o.trace.empty() ? 0 : trace_start + parse_time(o.trace_len, REAL_HZ);
        std::vector<uint64_t> t_on, t_edges, t_run, t_longest;
        std::vector<int> t_last;
        if (!o.trace.empty()) {
            for (size_t pos = 0; pos <= o.trace_leds.size();) {
                size_t comma = o.trace_leds.find(',', pos);
                if (comma == std::string::npos) comma = o.trace_leds.size();
                trace_leds.push_back(atoi(o.trace_leds.substr(pos, comma - pos).c_str()));
                pos = comma + 1;
            }
            trace = fopen(o.trace.c_str(), "w");
            if (!trace) { fprintf(stderr, "vsim: cannot write %s\n", o.trace.c_str()); return 1; }
            fprintf(trace, "$timescale 40 ns $end\n$scope module vsim $end\n");
            for (size_t k = 0; k < trace_leds.size(); k++)
                fprintf(trace, "$var wire 1 %c led%d $end\n", (char)('!' + k), trace_leds[k]);
            fprintf(trace, "$upscope $end\n$enddefinitions $end\n");
            t_on.assign(trace_leds.size(), 0); t_edges.assign(trace_leds.size(), 0);
            t_run.assign(trace_leds.size(), 0); t_longest.assign(trace_leds.size(), 0);
            t_last.assign(trace_leds.size(), -1);
        }
        auto t0 = std::chrono::steady_clock::now();
        for (uint64_t c = 0; c < headless_clocks; c++) {
            script.run_to(c);
            top->io_button = script.buttons;
            top->io_dip = script.dips;
            top->rst_n = c >= 16 && !script.reset;
            feed(c);
            top->usb_rx = serial_in.line;
            top->clk = 1;
            top->eval();
            top->clk = 0;
            top->eval();
            int b = rx.clock(top->usb_tx);
            if (b >= 0 && o.echo) { fputc(b, stdout); fflush(stdout); }
            unsigned l = top->led >> 4 & 1;
            cpu_clocks += l != led4;
            led4 = l;
            if (trace && c >= trace_start && c < trace_end) {
                std::string changes;
                for (size_t k = 0; k < trace_leds.size(); k++) {
                    int v = top->io_led >> trace_leds[k] & 1;
                    if (v != t_last[k]) {
                        if (t_last[k] >= 0) { t_edges[k]++; t_run[k] = 0; }
                        else t_run[k] = 0;
                        changes += std::string(1, (char)('0' + v)) + (char)('!' + k) + "\n";
                    }
                    t_run[k]++;
                    if (t_run[k] > t_longest[k]) t_longest[k] = t_run[k];
                    t_on[k] += v;
                    t_last[k] = v;
                }
                if (c == trace_start) fprintf(trace, "#0\n$dumpvars\n%s$end\n", changes.c_str());
                else if (!changes.empty()) fprintf(trace, "#%llu\n%s", (unsigned long long)(c - trace_start), changes.c_str());
            }
        }
        if (trace) {
            fclose(trace);
            uint64_t n = trace_end - trace_start;
            for (size_t k = 0; k < trace_leds.size(); k++)
                fprintf(stderr, "vsim: led%d over %llu clocks: on %llu (%.1f%%), %llu edges, longest level %llu clocks\n",
                        trace_leds[k], (unsigned long long)n, (unsigned long long)t_on[k], 100.0 * t_on[k] / n,
                        (unsigned long long)t_edges[k], (unsigned long long)t_longest[k]);
            fprintf(stderr, "vsim: wrote %s\n", o.trace.c_str());
        }
        double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        fprintf(stderr, "\nvsim: %llu clocks (%.3f s of board time) in %.2f s: %.2f MHz, %.0f%% of real time\n",
                (unsigned long long)headless_clocks, headless_clocks / REAL_HZ, dt, headless_clocks / dt / 1e6,
                100 * headless_clocks / dt / REAL_HZ);
        fprintf(stderr, "vsim: %llu cpu clocks\n", (unsigned long long)cpu_clocks);
        fprintf(stderr, "vsim: led %02x  io_led %06x  io_select %x  io_segment %02x\n",
                (unsigned)top->led, (unsigned)top->io_led, (unsigned)top->io_select, (unsigned)top->io_segment);
        top->final();
        delete top;
        return 0;
    }

    std::string pty_path;
    int pty = offline ? -1 : open_pty(pty_path);
    if (pty >= 0) fprintf(stderr, "vsim: serial on %s\n", pty_path.c_str());

    if (SDL_Init(SDL_INIT_VIDEO)) { fprintf(stderr, "SDL: %s\n", SDL_GetError()); return 1; }
    if (o.scale <= 0) o.scale = o.side_by_side ? 11.0 : 16.0;
    const double s = o.scale;
    const int boards_w = o.side_by_side ? 2 : 1;
    const int bw = (int)lround(BOARD_W * s), bh = (int)lround(BOARD_H * s);
    SDL_Window* win = SDL_CreateWindow("vsim", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                       boards_w * bw, bh, 0);
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

    // Layout, mm, from IoBoard.kt and AlchitryBoard.kt. Stacked like the real boards (and the
    // Alchitry board view): the Io drawn over the Cu, whose LEDs and reset button show through
    // the cut-out on the Io's right edge. --side-by-side puts the Io to the right instead.
    const double IOX = o.side_by_side ? BOARD_W : 0.0;
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

    // Inputs (the switches live in script.dips: the mouse and --at both change them)
    unsigned& dips = script.dips;
    bool buttons[5] = {};
    unsigned fresh = 0;        // buttons pressed this frame: a release in the same frame waits a frame
    unsigned late_release = 0;
    bool reset_held = false;
    int mouse_button = -1;     // which push button the mouse is holding (-1 none, 5 = Cu reset)

    Vbasic_riscve_16k16k* top = new Vbasic_riscve_16k16k;
    top->clk = 0;
    top->rst_n = 0;
    top->io_button = 0;
    top->io_dip = 0;
    top->usb_rx = 1;
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

    const int win_w = boards_w * bw, win_h = bh;
    std::vector<unsigned char> frame((size_t)win_w * win_h * 4);
    FILE* video = nullptr;
    if (!o.record.empty()) {
        std::string ext = o.record.substr(o.record.find_last_of('.') + 1);
        std::string enc = ext == "gif"
            ? "-vf 'split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none'"
            : "-vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -c:v libx264 -pix_fmt yuv420p -crf 20";
        char cmd[1024];
        snprintf(cmd, sizeof cmd,
                 "ffmpeg -loglevel error -y -f rawvideo -pixel_format bgra -video_size %dx%d "
                 "-framerate %d -i - %s '%s'", win_w, win_h, o.fps, enc.c_str(), o.record.c_str());
        video = popen(cmd, "w");
        if (!video) { perror("ffmpeg"); return 1; }
    }

    bool running = true;
    while (running) {
        for (int i = 0; i < 5; i++) if (late_release >> i & 1) buttons[i] = false;   // held one frame
        late_release = 0;
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) running = false;
            else if (e.type == SDL_KEYDOWN && (e.key.keysym.sym == SDLK_ESCAPE || e.key.keysym.sym == SDLK_q))
                running = false;
            else if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT) {
                int mx = e.button.x, my = e.button.y;
                for (int i = 0; i < 24; i++) if (inside(dip[i], mx, my)) dips ^= 1u << i;
                for (int i = 0; i < 5; i++)
                    if (inside(button[i], mx, my)) { buttons[i] = true; mouse_button = i; fresh |= 1u << i; }
                if (inside(cu_reset, mx, my)) { reset_held = true; mouse_button = 5; }
            } else if (e.type == SDL_MOUSEBUTTONUP && e.button.button == SDL_BUTTON_LEFT) {
                if (mouse_button == 5) reset_held = false;
                else if (mouse_button >= 0 && (fresh >> mouse_button & 1)) late_release |= 1u << mouse_button;
                else if (mouse_button >= 0) buttons[mouse_button] = false;
                mouse_button = -1;
            }
        }

        unsigned btn = 0;
        for (int i = 0; i < 5; i++) btn |= (unsigned)buttons[i] << i;
        fresh = 0;

        // Run clocks for most of a 60 Hz frame, in chunks, never ahead of the real board; offline,
        // exactly one frame of board time.
        auto frame_start = std::chrono::steady_clock::now();
        const auto budget = std::chrono::microseconds(14000);
        while (offline ? frame_clocks < frame_len
                       : std::chrono::steady_clock::now() - frame_start < budget) {
            if (o.throttle) {
                double real = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
                if (clocks > real * REAL_HZ) break;
            }
            if (pty >= 0) {                    // typed into the serial pty: out on usb_rx
                unsigned char buf[256];
                ssize_t got = read(pty, buf, sizeof buf);
                for (ssize_t k = 0; k < got; k++) serial_in.queue.push_back(buf[k]);
            }
            const uint64_t chunk = offline ? std::min<uint64_t>(4096, frame_len - frame_clocks) : 4096;
            for (uint64_t n = 0; n < chunk; n++) {
                script.run_to(clocks + n);
                top->io_button = btn | script.buttons;
                top->io_dip = dips;
                top->rst_n = !(reset_held || script.reset || power_on > 0);
                if (power_on > 0) power_on--;
                feed(clocks + n);
                top->usb_rx = serial_in.line;
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
            clocks += chunk;
            frame_clocks += chunk;
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
        SDL_Rect cu_dst = {0, 0, bw, bh}, io_dst = {(int)lround(IOX * s), 0, bw, bh};
        SDL_RenderCopy(ren, cu_tex, nullptr, &cu_dst);

        auto draw_pressed = [&](const Rect& r) {
            // the IDE's pressed button: a filled circle, radius 0.243 of the width
            double cx = (r.x + r.w / 2) * s, cy = (r.y + r.h / 2) * s, rad = 0.243386 * r.w * s;
            SDL_SetRenderDrawColor(ren, 0xAB, 0x6E, 0x1D, 255);
            for (int dy = -(int)rad; dy <= (int)rad; dy++) {
                int dx = (int)sqrt(rad * rad - dy * dy);
                SDL_RenderDrawLine(ren, (int)(cx - dx), (int)(cy + dy), (int)(cx + dx), (int)(cy + dy));
            }
        };

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
        for (int i = 0; i < 8; i++) draw_led(cu_led[i], led_level[24 + i]);
        draw_led(cu_done, 1.0f);
        if (reset_held) draw_pressed(cu_reset);
        SDL_RenderCopy(ren, io_tex, nullptr, &io_dst);      // over the Cu when stacked
        for (int i = 0; i < 24; i++) draw_led(io_led[i], led_level[i]);

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

        for (int i = 0; i < 5; i++) if (buttons[i]) draw_pressed(button[i]);

        if (offline) SDL_RenderReadPixels(ren, nullptr, SDL_PIXELFORMAT_ARGB8888, frame.data(), win_w * 4);
        if (video) {
            fwrite(frame.data(), 1, frame.size(), video);
            if (clocks >= record_clocks) running = false;
        }
        if (!o.snapshot.empty() && clocks >= snapshot_clocks) {
            // BGRA bytes (ARGB8888 little-endian) through ImageMagick, so any image type works
            char cmd[1024];
            snprintf(cmd, sizeof cmd, "convert -size %dx%d -depth 8 BGRA:- '%s'", win_w, win_h, o.snapshot.c_str());
            FILE* p = popen(cmd, "w");
            if (p) { fwrite(frame.data(), 1, frame.size(), p); pclose(p); }
            fprintf(stderr, "vsim: saved %s at %.3f s of board time\n", o.snapshot.c_str(), clocks / REAL_HZ);
            o.snapshot.clear();
            if (!video) running = false;
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

    if (video) {
        pclose(video);
        fprintf(stderr, "vsim: recorded %s, %.3f s of board time at %d fps\n", o.record.c_str(),
                clocks / REAL_HZ, o.fps);
    }
    top->final();
    delete top;
    SDL_Quit();
    return 0;
}
