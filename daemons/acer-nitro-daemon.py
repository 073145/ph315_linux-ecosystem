#!/usr/bin/env python3
# /usr/local/bin/acer-nitro-daemon — roda como root

import subprocess, threading, time, os, glob, json, pwd

PROFILE = "/sys/firmware/acpi/platform_profile"
LED     = "/sys/module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi/nitro_sense/turbo_led"
DAMX    = "/opt/damx/gui/DivAcerManagerMax"
USER    = "mpotiki"

def user_env():
    uid = subprocess.check_output(["id", "-u", USER], text=True).strip()
    try:
        pids = subprocess.check_output(["pgrep", "-u", USER], text=True).split()
        for pid in pids:
            env_file = f"/proc/{pid.strip()}/environ"
            if not os.path.exists(env_file):
                continue
            data = open(env_file, "rb").read()
            env = {}
            for item in data.split(b"\x00"):
                if b"=" in item:
                    k, v = item.split(b"=", 1)
                    env[k.decode(errors="replace")] = v.decode(errors="replace")
            if "WAYLAND_DISPLAY" in env:
                return env
    except Exception as e:
        print(f"user_env error: {e}", flush=True)
    env = os.environ.copy()
    env["HOME"] = f"/home/{USER}"
    env["USER"] = USER
    uid = subprocess.check_output(["id", "-u", USER], text=True).strip()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
    for f in glob.glob(f"/run/user/{uid}/wayland-*"):
        if not f.endswith(".lock"):
            env["WAYLAND_DISPLAY"] = os.path.basename(f)
            break
    return env

def read_profile():
    return open(PROFILE).read().strip()

def set_led(profile):
    """LED acende só em performance, apaga em qualquer outro perfil."""
    if not os.path.exists(LED):
        return
    val = "1" if profile == "performance" else "0"
    try:
        with open(LED, "w") as f:
            f.write(val + "\n")
        print(f"LED: {'ON' if val=='1' else 'OFF'} ({profile})", flush=True)
    except Exception as e:
        print(f"LED error: {e}", flush=True)

def set_profile(profile):
    if read_profile() != profile:
        open(PROFILE, "w").write(profile)
    set_led(profile)

# ── Turbo ────────────────────────────────────────────────────────────────────
def watch_turbo():
    last_known = read_profile()
    set_led(last_known)
    print(f"Turbo: pronto (profile={last_known})", flush=True)

    proc = subprocess.Popen(
        ["udevadm", "monitor", "--udev", "--subsystem-match=platform-profile"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
    )

    last_event = 0.0

    for line in iter(proc.stdout.readline, ""):
        if "change" not in line:
            continue
        now = time.monotonic()
        # Debounce: o botão físico dispara 2 eventos rápidos (~200ms entre eles)
        # Mudanças do DAMX chegam como evento único
        if now - last_event < 0.6:
            continue
        last_event = now
        time.sleep(0.15)

        actual = read_profile()

        if actual == last_known:
            # Firmware reverteu o perfil → é o botão físico → toggle performance/balanced
            new = "performance" if last_known != "performance" else "balanced"
            set_profile(new)
            last_known = new
            print(f"Turbo: botão → {new}", flush=True)
        else:
            # Mudança externa (DAMX, etc.) → sincroniza LED e atualiza estado
            set_led(actual)
            last_known = actual
            print(f"Turbo: externo → {actual}", flush=True)

# ── Predator Key ─────────────────────────────────────────────────────────────
def hyprctl(cmd, env):
    try:
        return subprocess.check_output(["hyprctl"] + cmd + ["-j"], env=env, text=True)
    except:
        return "{}"

def open_damx(env):
    pw = pwd.getpwnam(USER)
    def drop():
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
    subprocess.Popen([DAMX], env=env, preexec_fn=drop,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.1)
        clients = json.loads(hyprctl(["clients"], env))
        if any("DivAcerManagerMax" in c.get("class","") for c in clients):
            break

def focus_damx(env):
    subprocess.Popen(["hyprctl", "dispatch", "focuswindow", "class:DivAcerManagerMax"],
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.05)
    subprocess.Popen(["hyprctl", "dispatch", "fullscreen", "1"],
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def watch_predator():
    from evdev import InputDevice, list_devices, ecodes
    dev = None
    for path in list_devices():
        try:
            d = InputDevice(path)
            if 186 in d.capabilities().get(ecodes.EV_KEY, []):
                dev = d
                break
        except:
            pass
    if not dev:
        print("Predator Key: device não encontrado!", flush=True)
        return

    print(f"Predator Key: pronto ({dev.name})", flush=True)

    for ev in dev.read_loop():
        if ev.type != ecodes.EV_KEY or ev.code != 186 or ev.value != 1:
            continue
        print("Predator Key: pressionado", flush=True)
        env = user_env()

        try:
            clients  = json.loads(hyprctl(["clients"], env))
            damx_win = next((c for c in clients if "DivAcerManagerMax" in c.get("class","")), None)
            active   = json.loads(hyprctl(["activewindow"], env))
            focused  = "DivAcerManagerMax" in active.get("class", "")

            if not damx_win:
                open_damx(env)
                focus_damx(env)
                print("DAMX: aberto", flush=True)
            elif focused:
                subprocess.Popen(["hyprctl", "dispatch", "closewindow", "class:DivAcerManagerMax"],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("DAMX: fechado", flush=True)
            else:
                ws   = json.loads(hyprctl(["activeworkspace"], env)).get("id")
                addr = damx_win.get("address", "")
                subprocess.Popen(["hyprctl", "dispatch", "movetoworkspacesilent",
                                  f"{ws},address:{addr}"],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.05)
                focus_damx(env)
                print("DAMX: trazido e focado", flush=True)

        except Exception as e:
            print(f"DAMX error: {e}", flush=True)

# ── Boot ─────────────────────────────────────────────────────────────────────
set_led(read_profile())
print(f"Boot: profile={read_profile()}", flush=True)

threading.Thread(target=watch_turbo,    daemon=True).start()
threading.Thread(target=watch_predator, daemon=True).start()

while True:
    time.sleep(60)
