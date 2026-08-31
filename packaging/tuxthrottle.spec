%global appdir  /opt/tuxthrottle
%global gittag   %{?_gittag}%{!?_gittag:main}

Name:           tuxthrottle
Version:        %{?_version}%{!?_version:0.1.0}
Release:        %{?_release}%{!?_release:1}%{?dist}
Summary:        Gaming / power / thermal control for the Dell G15 5515 on Nobara Linux

License:        MIT
URL:            https://github.com/BeanGreen247/tuxthrottle
Source0:        %{url}/archive/%{gittag}/%{name}-%{gittag}.tar.gz

BuildArch:      noarch

# GUI + core
Requires:       python3
Requires:       python3-tkinter
Requires:       (python3-ttkbootstrap or python3-pip)
# used by helpers / tweaks at runtime (best-effort; the app degrades without them)
Recommends:     openrgb
Recommends:     ryzenadj
Recommends:     libsmbios
Recommends:     python3-pyside6
Recommends:     polkit
Recommends:     gamemode

# The in-app "tweaks" (systemd units, sudoers, kwriteconfig6 …) stay strictly
# opt-in and are applied from the GUI / apply_tweak.py — never from %post.

%description
TuxThrottle is a checkbox GUI plus a tray monitor and a G-key listener that
applies hardware-specific tweaks, drivers and gaming setup to the Dell G15 5515
Ryzen Edition (Ryzen 7 5800H + RTX 3050 Ti Mobile) running Nobara Linux.

It covers CPU TDP (ryzenadj), battery charge limit, NVIDIA power limit where the
GPU allows it, a Feral GameMode bridge, a closed-loop fan curve with AC/battery
auto-switch, per-game auto-profiles, named full-state profiles with
snapshot/rollback, thermal-event notifications, KDE Plasma 6 desktop tweaks, and
a headless CLI (tuxthrottlectl). Every check/apply command targets that one
board; use --report to see what applies on the running machine.

%prep
%autosetup -n %{name}-%{gittag}

%build
# nothing to build — pure Python + JSON + shell

%install
install -d %{buildroot}%{appdir}
# ship the runnable tree; leave dev-only bits out (mirrors install.sh)
cp -a *.py config assets %{buildroot}%{appdir}/
install -m 0644 README.md CLAUDE.md LICENSE %{buildroot}%{appdir}/ 2>/dev/null || :
printf '%%s\n' "%{version}-%{release}" > %{buildroot}%{appdir}/.version

# launcher + CLI
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/tuxthrottle <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/python3 /opt/tuxthrottle/tuxthrottle.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/tuxthrottle
ln -s %{appdir}/tuxthrottlectl.py %{buildroot}%{_bindir}/tuxthrottlectl

# desktop entry + icons
install -d %{buildroot}%{_datadir}/applications
sed 's#__TOOLKIT_DIR__#%{appdir}#g' assets/tuxthrottle.desktop \
    > %{buildroot}%{_datadir}/applications/tuxthrottle.desktop
for s in 16 24 32 48 64 128 256 512; do
    if [ -f assets/icon-${s}.png ]; then
        install -Dm0644 assets/icon-${s}.png \
            %{buildroot}%{_datadir}/icons/hicolor/${s}x${s}/apps/tuxthrottle.png
    fi
done
install -Dm0644 assets/icon.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/tuxthrottle.svg 2>/dev/null || :

%files
%license LICENSE
%doc README.md
%{appdir}
%{_bindir}/tuxthrottle
%{_bindir}/tuxthrottlectl
%{_datadir}/applications/tuxthrottle.desktop
%{_datadir}/icons/hicolor/*/apps/tuxthrottle.*

%changelog
* Sun Aug 31 2026 BeanGreen247 <mozdrent@gmail.com> - 0.1.0-1
- Initial RPM packaging: GUI + tuxthrottlectl + tray + hotkey listener + daemon.
- Tweaks remain opt-in and are applied from the app, never from %%post.
