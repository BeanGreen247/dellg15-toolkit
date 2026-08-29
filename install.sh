#!/usr/bin/env bash
#
# System-wide installer for the Dell G15 Toolkit (Nobara Linux).
#
#   sudo ./install.sh            # install for all users, add to the KDE menu
#   sudo ./install.sh --uninstall
#
# Installs to /opt/dellg15-toolkit, a launcher at /usr/local/bin/dellg15-toolkit,
# a .desktop entry in /usr/share/applications (so every user can search for it
# in KDE), and the icon into the hicolor theme.
#
set -euo pipefail

APPID="dellg15-toolkit"
LIBDIR="/opt/${APPID}"
BIN="/usr/local/bin/${APPID}"
DESKTOP="/usr/share/applications/${APPID}.desktop"
ICONBASE="/usr/share/icons/hicolor"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

c_ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
c_info() { printf '\033[34m  →\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m  !\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m  ✗\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { c_err "Run with sudo: sudo $0 ${*:-}"; exit 1; }

ICON_SIZES=(16 24 32 48 64 128 256 512)

refresh_caches() {
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    gtk-update-icon-cache -q -t -f "$ICONBASE" >/dev/null 2>&1 || true
    # make it show up in KDE search without a re-login
    local u="${SUDO_USER:-}"
    if [[ -n "$u" ]] && command -v kbuildsycoca6 >/dev/null 2>&1; then
        sudo -u "$u" env DISPLAY="${DISPLAY:-:0}" kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
    fi
}

do_uninstall() {
    c_info "Removing ${APPID}…"
    rm -f "$BIN" "$DESKTOP"
    for s in "${ICON_SIZES[@]}"; do rm -f "${ICONBASE}/${s}x${s}/apps/${APPID}.png"; done
    rm -f "${ICONBASE}/scalable/apps/${APPID}.svg"
    rm -rf "$LIBDIR"
    refresh_caches
    c_ok "Uninstalled the app. (Tweaks/services applied from inside the tool are left"
    c_ok " as-is — 'sudo ./uninstall.sh --purge' removes those too.)"
}

do_install() {
    [[ -f "$SRC/dellg15_toolkit.py" ]] || { c_err "run this from the toolkit source dir"; exit 1; }

    # ---- dependencies -------------------------------------------------------
    c_info "Checking dependencies…"
    if ! python3 -c 'import tkinter' 2>/dev/null; then
        c_info "installing python3-tkinter"
        dnf install -y -q python3-tkinter || { c_err "could not install python3-tkinter"; exit 1; }
    fi
    if ! python3 -c 'import ttkbootstrap' 2>/dev/null; then
        c_info "installing ttkbootstrap (pip, system-wide — it isn't packaged for Fedora/Nobara)"
        python3 -m pip install --break-system-packages --root-user-action=ignore -q ttkbootstrap \
            || { c_err "ttkbootstrap install failed — the GUI needs it"; exit 1; }
    fi
    python3 -c 'import ttkbootstrap' 2>/dev/null && c_ok "ttkbootstrap OK" || { c_err "ttkbootstrap still not importable"; exit 1; }
    # optional extras
    python3 -c 'import PySide6' 2>/dev/null || dnf install -y -q python3-pyside6 \
        || c_warn "python3-pyside6 not installed — the tray monitor (tray_monitor.py) won't run"
    python3 -c 'import evdev'   2>/dev/null || dnf install -y -q python3-evdev \
        || c_warn "python3-evdev not installed — the G-key HotkeyListener tweak won't run"

    # ---- files ------------------------------------------------------------
    c_info "Installing to ${LIBDIR}"
    rm -rf "$LIBDIR"
    install -d "$LIBDIR"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --exclude='.git' --exclude='__pycache__' --exclude='install.sh' "$SRC"/ "$LIBDIR"/
    else
        cp -a "$SRC"/. "$LIBDIR"/ && rm -rf "$LIBDIR/.git" "$LIBDIR/install.sh"
    fi
    find "$LIBDIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    chmod -R a+rX "$LIBDIR"
    c_ok "copied $(find "$LIBDIR" -type f | wc -l) files"

    cat > "$BIN" <<EOF
#!/usr/bin/env bash
# Dell G15 Toolkit launcher (self-elevates via pkexec/sudo)
exec /usr/bin/python3 "${LIBDIR}/dellg15_toolkit.py" "\$@"
EOF
    chmod 0755 "$BIN"
    c_ok "launcher: ${BIN}"

    # ---- icon -----------------------------------------------------------
    for s in "${ICON_SIZES[@]}"; do
        if [[ -f "$LIBDIR/assets/icon-${s}.png" ]]; then
            install -Dm644 "$LIBDIR/assets/icon-${s}.png" "${ICONBASE}/${s}x${s}/apps/${APPID}.png"
        fi
    done
    [[ -f "$LIBDIR/assets/icon.svg" ]] && install -Dm644 "$LIBDIR/assets/icon.svg" "${ICONBASE}/scalable/apps/${APPID}.svg"
    c_ok "icon installed into ${ICONBASE}"

    # ---- .desktop -----------------------------------------------------
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Dell G15 Toolkit
GenericName=Hardware & Gaming Tweaks
Comment=Tweaks, drivers, RGB keyboard and gaming setup for the Dell G15 5515 on Nobara
Exec=${APPID}
TryExec=${APPID}
Icon=${APPID}
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=dell;g15;rgb;gamemode;performance;nvidia;tweak;keyboard;backlight;
StartupNotify=true
EOF
    chmod 0644 "$DESKTOP"
    desktop-file-validate "$DESKTOP" >/dev/null 2>&1 && c_ok "desktop entry: ${DESKTOP}" \
        || c_warn "desktop entry written but desktop-file-validate flagged it"

    refresh_caches
    echo
    c_ok "Installed. Launch it from the KDE menu ('Dell G15 Toolkit') or run: ${APPID}"
    c_ok "All users on this system can now find it."
}

case "${1:-}" in
    --uninstall|-u|uninstall) do_uninstall ;;
    ""|--install|-i|install)  do_install ;;
    *) c_err "unknown option: $1"; echo "usage: sudo $0 [--install|--uninstall]"; exit 1 ;;
esac
