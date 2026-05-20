"""wxPython configuration builder for PowerCom ttcom.conf files."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import wx
except ImportError as error:
    raise SystemExit("wxPython is required for the PowerCom configuration UI.") from error

from powercom_config_model import (
    ConfigDocument,
    DEFAULT_CORE,
    DEFAULT_FEATURES,
    ServerConfig,
    parse_document,
    render_document,
    save_document,
    validate_document,
)


BACKENDS = [
    "auto",
    "SAPI",
    "ONE_CORE",
    "NVDA",
    "JAWS",
    "UIA",
    "SPEECH_DISPATCHER",
    "VOICE_OVER",
    "ORCA",
]

CONFIG_FILE_NAME = "ttcom.conf"

FEATURE_FIELDS = [
    ("speech", "Enable speech:", "check", True),
    ("speechengine", "Speech backend:", "combo", "auto", BACKENDS),
    ("speechinterrupt", "Interrupt current speech:", "check", True),
    ("speechdmodule", "Speech Dispatcher module:", "text", ""),
    ("speechvoice", "Speech voice:", "text", "-1"),
    ("speechrate", "Speech rate:", "text", ""),
    ("speechvolume", "Speech volume:", "text", ""),
    ("speechpitch", "Speech pitch:", "text", ""),
    ("nospeak", "Events not spoken:", "text", ""),
    ("sounds", "Enable sounds:", "check", True),
    ("soundpack", "Sound pack:", "text", "default"),
    ("soundvolume", "Sound volume:", "text", "100"),
    ("playbacktype", "Playback type:", "choice", "overlapping", ["overlapping", "interrupting", "oneByOne"]),
    ("nosound", "Events without sounds:", "text", ""),
    ("notifyloginout", "Notify login/logout:", "check", True),
    ("notifymessage", "Notify messages:", "check", True),
    ("systemnotify", "System notifications:", "check", False),
    ("ntfy", "Ntfy notifications:", "check", False),
    ("ntfytopic", "Ntfy topic:", "text", ""),
    ("ntfyurl", "Ntfy URL:", "text", "https://ntfy.sh"),
    ("ntfyuser", "Ntfy username:", "text", ""),
    ("ntfypassword", "Ntfy password:", "password", ""),
    ("prowl", "Prowl notifications:", "check", False),
    ("prowlkey", "Prowl API key:", "password", ""),
    ("mgnotify", "MG Notify notifications:", "check", False),
    ("mgnotifykey", "MG Notify key:", "password", ""),
    ("pushover", "Pushover notifications:", "check", False),
    ("pushoveruser", "Pushover user key:", "password", ""),
    ("pushovertoken", "Pushover API token:", "password", ""),
    ("pushoverdevice", "Pushover device:", "text", ""),
    ("pushoversound", "Pushover sound:", "text", ""),
    ("pushoverpriority", "Pushover priority:", "choice", "", ["", "-2", "-1", "0", "1", "2"]),
    ("pushoverretry", "Pushover emergency retry seconds:", "text", ""),
    ("pushoverexpire", "Pushover emergency expire seconds:", "text", ""),
    ("log", "Enable event logging:", "check", True),
    ("maxlogsize", "Max log size MB:", "text", "4"),
    ("maxlogfiles", "Max log files:", "text", "5"),
]

CHOICE_LABELS = {
    "autologin": [
        ("0", "Manual login only"),
        ("1", "Auto login"),
        ("2", "Auto login after kick"),
    ],
    "gender": [
        ("", "Unspecified"),
        ("n", "Neutral"),
        ("m", "Male"),
        ("f", "Female"),
    ],
    "pushoverpriority": [
        ("", "Default priority"),
        ("-2", "Lowest priority, no alert"),
        ("-1", "Low priority"),
        ("0", "Normal priority"),
        ("1", "High priority"),
        ("2", "Emergency priority"),
    ],
    "silent": [
        ("0", "Always print events"),
        ("1", "Only print events for the current server"),
        ("2", "Never print regular events"),
        ("3", "Full silence except critical output"),
    ],
}


def configure_choice(ctrl: wx.Choice, key: str, choices: list[str], default: str) -> None:
    ctrl.Clear()
    for value, label in CHOICE_LABELS.get(key, [(str(choice), str(choice)) for choice in choices]):
        ctrl.Append(label, value)
    set_choice_value(ctrl, default)


def set_choice_value(ctrl: wx.Choice, value: str) -> bool:
    value = str(value)
    for index in range(ctrl.GetCount()):
        client_data = ctrl.GetClientData(index)
        candidate = str(client_data) if client_data is not None else ctrl.GetString(index)
        if candidate == value:
            ctrl.SetSelection(index)
            return True
    if ctrl.GetCount():
        ctrl.SetSelection(0)
    return False


def get_choice_value(ctrl: wx.Choice) -> str:
    selection = ctrl.GetSelection()
    if selection == wx.NOT_FOUND:
        return ""
    client_data = ctrl.GetClientData(selection)
    return str(client_data) if client_data is not None else ctrl.GetStringSelection()


def _accessible_name(label: str) -> str:
    name = label.rstrip().rstrip(":").strip()
    return name.replace("&", "")


def _sample_document() -> ConfigDocument:
    return ConfigDocument(
        servers=[ServerConfig("pub_US", {"host": "tt5us.bearware.dk", "tcpport": "10335", "username": "bearware"})]
    )


def _candidate_config_paths() -> list[Path]:
    candidates = [Path.cwd() / CONFIG_FILE_NAME]

    executable_path = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    executable_dir = executable_path.resolve().parent
    candidates.append(executable_dir / CONFIG_FILE_NAME)

    module_dir = Path(__file__).resolve().parent
    candidates.append(module_dir / CONFIG_FILE_NAME)

    if executable_dir.name.lower() == "powercom" and executable_dir.parent.name.lower() == "dist":
        candidates.append(executable_dir.parent.parent / CONFIG_FILE_NAME)

    unique_candidates = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)
    return unique_candidates


def find_config_path() -> Path | None:
    for path in _candidate_config_paths():
        if path.exists():
            return path
    return None


def resolve_config_path(argv: list[str] | None = None) -> Path | None:
    args = list(argv or [])
    if args and args[0] in {"--config", "--config-ui", "--config-builder"}:
        args = args[1:]
    if args:
        return Path(args[0]).expanduser().resolve()
    return find_config_path()


class FeatureOptionsPanel(wx.ScrolledWindow):
    def __init__(self, parent, optional: bool = False):
        super().__init__(parent, style=wx.VSCROLL)
        self.optional = optional
        self.controls: dict[str, wx.Window] = {}
        self.enable_box = None
        self.SetScrollRate(0, 20)

        root = wx.BoxSizer(wx.VERTICAL)
        if optional:
            self.enable_box = wx.CheckBox(self, label="&Write PowerCom overrides for this server")
            self.enable_box.SetName("Write PowerCom overrides for this server")
            self.enable_box.Bind(wx.EVT_CHECKBOX, self.on_enable_changed)
            root.Add(self.enable_box, 0, wx.ALL, 6)

        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        grid.AddGrowableCol(1, 1)
        for spec in FEATURE_FIELDS:
            key, label, field_type, default, *rest = spec
            accessible = _accessible_name(label)
            if field_type == "check":
                ctrl = wx.CheckBox(self, label=accessible)
                ctrl.SetName(accessible)
                ctrl.SetValue(bool(default))
                grid.Add((1, 1), 0)
                grid.Add(ctrl, 0, wx.EXPAND)
            else:
                grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
                if field_type == "choice":
                    ctrl = wx.Choice(self)
                    configure_choice(ctrl, key, rest[0], str(default))
                elif field_type == "combo":
                    ctrl = wx.ComboBox(self, value=str(default), choices=rest[0], style=wx.CB_DROPDOWN)
                elif field_type == "password":
                    ctrl = wx.TextCtrl(self, value=str(default), style=wx.TE_PASSWORD)
                else:
                    ctrl = wx.TextCtrl(self, value=str(default))
                ctrl.SetName(accessible)
                grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        root.Add(grid, 1, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(root)
        self.on_enable_changed(None)

    def on_enable_changed(self, event) -> None:
        enabled = True if self.enable_box is None else self.enable_box.GetValue()
        for ctrl in self.controls.values():
            ctrl.Enable(enabled)
        if event is not None:
            event.Skip()

    def load_values(self, values: dict[str, str]) -> None:
        if self.enable_box is not None:
            self.enable_box.SetValue(bool(values))
        merged = dict(DEFAULT_FEATURES)
        merged.update(values)
        for key, ctrl in self.controls.items():
            value = merged.get(key, "")
            if isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(str(value).strip().lower() in {"1", "true", "yes", "y"})
            elif isinstance(ctrl, wx.Choice):
                set_choice_value(ctrl, str(value))
            else:
                ctrl.SetValue(str(value))
        self.on_enable_changed(None)

    def get_values(self) -> dict[str, str]:
        if self.enable_box is not None and not self.enable_box.GetValue():
            return {}
        values = {}
        for key, ctrl in self.controls.items():
            if isinstance(ctrl, wx.CheckBox):
                values[key] = "true" if ctrl.GetValue() else "false"
            elif isinstance(ctrl, wx.Choice):
                values[key] = get_choice_value(ctrl)
            else:
                values[key] = ctrl.GetValue().strip()
        return {key: value for key, value in values.items() if value != ""}


class ServerEditorPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.controls: dict[str, wx.Window] = {}

        notebook = wx.Notebook(self)
        core_page = wx.ScrolledWindow(notebook, style=wx.VSCROLL)
        core_page.SetScrollRate(0, 20)
        core_sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        grid.AddGrowableCol(1, 1)

        short_name_static = wx.StaticText(core_page, label="Short name:")
        self.name_ctrl = wx.TextCtrl(core_page)
        self.name_ctrl.SetName(_accessible_name("Short name:"))
        grid.Add(short_name_static, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.name_ctrl, 1, wx.EXPAND)

        self._add_text(grid, core_page, "host", "Host:")
        self._add_text(grid, core_page, "tcpport", "TCP port:", "10333")
        self._add_text(grid, core_page, "udpport", "UDP port:")
        self._add_text(grid, core_page, "username", "Username:")
        self._add_text(grid, core_page, "password", "Password:", password=True)
        self._add_text(grid, core_page, "nickname", "Nickname:")
        self._add_choice(grid, core_page, "autologin", "Auto login:", ["0", "1", "2"], "0")
        self._add_check(grid, core_page, "encrypted", "Encrypted/pro server:")
        self._add_check(grid, core_page, "hidden", "Hidden from summaries:")
        self._add_choice(grid, core_page, "silent", "Silent mode:", ["0", "1", "2", "3"], "0")
        self._add_choice(grid, core_page, "gender", "Gender:", ["", "n", "m", "f"], "")
        self._add_text(grid, core_page, "statusmode", "Status mode:")
        self._add_text(grid, core_page, "statusmsg", "Status message:")
        self._add_text(grid, core_page, "channel", "Channel path:")
        self._add_text(grid, core_page, "chanid", "Channel ID:")
        self._add_text(grid, core_page, "chanpassword", "Channel password:", password=True)

        core_sizer.Add(grid, 1, wx.EXPAND | wx.ALL, 8)
        core_page.SetSizer(core_sizer)
        notebook.AddPage(core_page, "Connection")
        self.feature_panel = FeatureOptionsPanel(notebook, optional=True)
        notebook.AddPage(self.feature_panel, "PowerCom overrides")

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(notebook, 1, wx.EXPAND)
        self.SetSizer(root)

    def set_editor_enabled(self, enabled: bool) -> None:
        self.name_ctrl.Enable(enabled)
        for ctrl in self.controls.values():
            ctrl.Enable(enabled)
        self.feature_panel.Enable(enabled)

    def focus_short_name(self) -> None:
        self.name_ctrl.SetFocus()
        self.name_ctrl.SelectAll()

    def _add_text(self, grid, parent, key: str, label: str, default: str = "", password: bool = False) -> None:
        style = wx.TE_PASSWORD if password else 0
        accessible = _accessible_name(label)
        static = wx.StaticText(parent, label=label)
        ctrl = wx.TextCtrl(parent, value=default, style=style)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add(static, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(ctrl, 1, wx.EXPAND)

    def _add_choice(self, grid, parent, key: str, label: str, choices: list[str], default: str) -> None:
        accessible = _accessible_name(label)
        static = wx.StaticText(parent, label=label)
        ctrl = wx.Choice(parent)
        configure_choice(ctrl, key, choices, default)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add(static, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(ctrl, 1, wx.EXPAND)

    def _add_check(self, grid, parent, key: str, label: str) -> None:
        accessible = _accessible_name(label)
        ctrl = wx.CheckBox(parent, label=accessible)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add((1, 1), 0)
        grid.Add(ctrl, 0, wx.EXPAND)

    def load_server(self, server: ServerConfig | None) -> None:
        has_server = server is not None
        self.set_editor_enabled(has_server)
        if server is None:
            self.name_ctrl.SetValue("")
            values = {}
            overrides = {}
        else:
            self.name_ctrl.SetValue(server.name)
            values = server.values
            overrides = server.overrides
        for key, ctrl in self.controls.items():
            value = values.get(key, "")
            if isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(str(value).strip().lower() in {"1", "true", "yes", "y"})
            elif isinstance(ctrl, wx.Choice):
                set_choice_value(ctrl, str(value))
            else:
                ctrl.SetValue(str(value))
        self.feature_panel.load_values(overrides)
        if not has_server:
            self.set_editor_enabled(False)

    def get_server(self) -> ServerConfig:
        values = {}
        for key, ctrl in self.controls.items():
            if isinstance(ctrl, wx.CheckBox):
                values[key] = "1" if ctrl.GetValue() else "0"
            elif isinstance(ctrl, wx.Choice):
                values[key] = get_choice_value(ctrl)
            else:
                values[key] = ctrl.GetValue().strip()
        values = {key: value for key, value in values.items() if value != ""}
        return ServerConfig(self.name_ctrl.GetValue().strip(), values, self.feature_panel.get_values())


class DefaultsPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.controls: dict[str, wx.Window] = {}

        notebook = wx.Notebook(self)
        core_page = wx.Panel(notebook)
        self.feature_panel = FeatureOptionsPanel(notebook)
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        grid.AddGrowableCol(1, 1)
        self._add_choice(grid, core_page, "autologin", "Default auto login:", ["0", "1", "2"], "0")
        self._add_check(grid, core_page, "hidden", "Hidden from summaries:")
        self._add_choice(grid, core_page, "silent", "Silent mode:", ["0", "1", "2", "3"], "0")
        self._add_text(grid, core_page, "nickname", "Default nickname:", "PowerCom User")
        core_page.SetSizer(grid)
        notebook.AddPage(core_page, "Server defaults")
        notebook.AddPage(self.feature_panel, "PowerCom defaults")

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(notebook, 1, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(root)

    def focus_first_control(self) -> None:
        ctrl = next(iter(self.controls.values()), None)
        if ctrl is not None:
            ctrl.SetFocus()

    def _add_text(self, grid, parent, key: str, label: str, default: str = "") -> None:
        accessible = _accessible_name(label)
        static = wx.StaticText(parent, label=label)
        ctrl = wx.TextCtrl(parent, value=default)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add(static, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        grid.Add(ctrl, 1, wx.EXPAND | wx.ALL, 4)

    def _add_choice(self, grid, parent, key: str, label: str, choices: list[str], default: str) -> None:
        accessible = _accessible_name(label)
        static = wx.StaticText(parent, label=label)
        ctrl = wx.Choice(parent)
        configure_choice(ctrl, key, choices, default)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add(static, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        grid.Add(ctrl, 1, wx.EXPAND | wx.ALL, 4)

    def _add_check(self, grid, parent, key: str, label: str) -> None:
        accessible = _accessible_name(label)
        ctrl = wx.CheckBox(parent, label=accessible)
        ctrl.SetName(accessible)
        self.controls[key] = ctrl
        grid.Add((1, 1), 0)
        grid.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)

    def load_values(self, defaults: dict[str, str], feature_defaults: dict[str, str]) -> None:
        merged = dict(DEFAULT_CORE)
        merged.update(defaults)
        for key, ctrl in self.controls.items():
            value = merged.get(key, "")
            if isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(str(value).strip().lower() in {"1", "true", "yes", "y"})
            elif isinstance(ctrl, wx.Choice):
                set_choice_value(ctrl, str(value))
            else:
                ctrl.SetValue(str(value))
        self.feature_panel.load_values(feature_defaults)

    def get_defaults(self) -> tuple[dict[str, str], dict[str, str]]:
        defaults = {}
        for key, ctrl in self.controls.items():
            if isinstance(ctrl, wx.CheckBox):
                defaults[key] = "1" if ctrl.GetValue() else "0"
            elif isinstance(ctrl, wx.Choice):
                defaults[key] = get_choice_value(ctrl)
            else:
                defaults[key] = ctrl.GetValue().strip()
        defaults = {key: value for key, value in defaults.items() if value != ""}
        return defaults, self.feature_panel.get_values()


class ConfigFrame(wx.Frame):
    def __init__(self, config_path: Path | None = None):
        super().__init__(None, title="PowerCom Configuration Builder", size=(980, 720))
        self.path: Path | None = None
        self.document = _sample_document()
        self.current_server_index: int | None = None
        self._initial_focus_set = False
        self._loading_document = False
        self._preview_update_pending = False
        self._preview_update_timer: wx.CallLater | None = None
        self._closing = False

        status_bar = self.CreateStatusBar()
        status_bar.SetName("Status messages")
        self._build_menu()
        self._build_layout()
        self._bind_events()
        status_text = self.load_initial_document(config_path)
        self.update_preview()
        self.SetStatusText(status_text)
        self.Centre()

    def _build_menu(self) -> None:
        menu_bar = wx.MenuBar()
        file_menu = wx.Menu()
        self.new_id = wx.NewId()
        self.open_id = wx.NewId()
        self.save_id = wx.NewId()
        self.save_as_id = wx.NewId()
        self.preview_id = wx.NewId()
        file_menu.Append(self.new_id, "&New configuration\tCtrl+N")
        file_menu.Append(self.open_id, "&Open configuration...\tCtrl+O")
        file_menu.Append(self.save_id, "&Save configuration\tCtrl+S")
        file_menu.Append(self.save_as_id, "Save configuration &As...\tCtrl+Shift+S")
        file_menu.AppendSeparator()
        file_menu.Append(self.preview_id, "&Refresh Preview\tF5")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        menu_bar.Append(file_menu, "&File")
        self.SetMenuBar(menu_bar)
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    (wx.ACCEL_CTRL, ord("N"), self.new_id),
                    (wx.ACCEL_CTRL, ord("O"), self.open_id),
                    (wx.ACCEL_CTRL, ord("S"), self.save_id),
                    (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("S"), self.save_as_id),
                    (wx.ACCEL_NORMAL, wx.WXK_F5, self.preview_id),
                ]
            )
        )

    def _build_layout(self) -> None:
        root_panel = wx.Panel(self)
        self.notebook = wx.Notebook(root_panel)
        notebook = self.notebook
        self.defaults_panel = DefaultsPanel(notebook)
        notebook.AddPage(self.defaults_panel, "Defaults")

        servers_page = wx.Panel(notebook)
        server_list_static = wx.StaticText(servers_page, label="Server &list:")
        self.server_list = wx.ListBox(servers_page, style=wx.LB_SINGLE)
        self.server_list.SetName("Servers")
        self.editor_hint = wx.StaticText(
            servers_page,
            label="Select or add a server to begin editing.",
        )
        self.editor_hint.SetName("Select or add a server to begin editing.")
        self.server_editor = ServerEditorPanel(servers_page)
        server_root = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(server_list_static, 0, wx.ALL, 4)
        left.Add(self.server_list, 1, wx.EXPAND | wx.ALL, 4)
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.add_server_btn = wx.Button(servers_page, label="&Add server")
        self.add_server_btn.SetName("Add server to list")
        self.remove_server_btn = wx.Button(servers_page, label="&Remove selected server")
        self.remove_server_btn.SetName("Remove selected server from list")
        button_row.Add(self.add_server_btn, 1, wx.RIGHT, 4)
        button_row.Add(self.remove_server_btn, 1, wx.LEFT, 4)
        left.Add(button_row, 0, wx.EXPAND | wx.ALL, 4)
        server_root.Add(left, 0, wx.EXPAND | wx.ALL, 6)
        editor_column = wx.BoxSizer(wx.VERTICAL)
        editor_column.Add(self.editor_hint, 0, wx.ALL, 4)
        editor_column.Add(self.server_editor, 1, wx.EXPAND | wx.ALL, 0)
        server_root.Add(editor_column, 1, wx.EXPAND | wx.ALL, 6)
        servers_page.SetSizer(server_root)
        notebook.AddPage(servers_page, "Servers")

        preview_page = wx.Panel(notebook)
        preview_static = wx.StaticText(preview_page, label="Configuration pre&view:")
        self.preview_ctrl = wx.TextCtrl(preview_page, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.preview_ctrl.SetName("Configuration preview")
        preview_root = wx.BoxSizer(wx.VERTICAL)
        preview_root.Add(preview_static, 0, wx.ALL, 6)
        preview_root.Add(self.preview_ctrl, 1, wx.EXPAND | wx.ALL, 6)
        preview_page.SetSizer(preview_root)
        notebook.AddPage(preview_page, "Preview")

        action_row = wx.BoxSizer(wx.HORIZONTAL)
        self.action_buttons = {}
        for button_id, label, accessible in [
            (self.new_id, "&New configuration", "New configuration"),
            (self.open_id, "&Open configuration...", "Open configuration"),
            (self.save_id, "&Save configuration", "Save configuration"),
            (self.save_as_id, "Save configuration as...", "Save configuration as"),
            (self.preview_id, "Refresh &Preview", "Refresh configuration preview"),
            (wx.ID_EXIT, "&Close configurator", "Close configurator"),
        ]:
            button = wx.Button(root_panel, id=button_id, label=label)
            button.SetName(accessible)
            self.action_buttons[button_id] = button
            action_row.Add(button, 0, wx.ALL, 4)

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(notebook, 1, wx.EXPAND)
        root.Add(action_row, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        root_panel.SetSizer(root)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(root_panel, 1, wx.EXPAND)
        self.SetSizer(frame_sizer)

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_MENU, self.on_new, id=self.new_id)
        self.Bind(wx.EVT_MENU, self.on_open, id=self.open_id)
        self.Bind(wx.EVT_MENU, self.on_save, id=self.save_id)
        self.Bind(wx.EVT_MENU, self.on_save_as, id=self.save_as_id)
        self.Bind(wx.EVT_MENU, self.on_refresh_preview, id=self.preview_id)
        self.Bind(wx.EVT_MENU, self.on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_BUTTON, self.on_new, id=self.new_id)
        self.Bind(wx.EVT_BUTTON, self.on_open, id=self.open_id)
        self.Bind(wx.EVT_BUTTON, self.on_save, id=self.save_id)
        self.Bind(wx.EVT_BUTTON, self.on_save_as, id=self.save_as_id)
        self.Bind(wx.EVT_BUTTON, self.on_refresh_preview, id=self.preview_id)
        self.Bind(wx.EVT_BUTTON, self.on_exit, id=wx.ID_EXIT)
        self.add_server_btn.Bind(wx.EVT_BUTTON, self.on_add_server)
        self.remove_server_btn.Bind(wx.EVT_BUTTON, self.on_remove_server)
        self.server_list.Bind(wx.EVT_LISTBOX, self.on_server_selected)
        self._bind_auto_preview_events()
        self.Bind(wx.EVT_SHOW, self.on_show)
        self.Bind(wx.EVT_CLOSE, self.on_exit)

    def _bind_auto_preview_events(self) -> None:
        for ctrl in self._auto_preview_controls():
            if isinstance(ctrl, wx.CheckBox):
                ctrl.Bind(wx.EVT_CHECKBOX, self.on_auto_preview_change)
            elif isinstance(ctrl, wx.Choice):
                ctrl.Bind(wx.EVT_CHOICE, self.on_auto_preview_change)
            elif isinstance(ctrl, wx.ComboBox):
                ctrl.Bind(wx.EVT_TEXT, self.on_auto_preview_change)
                ctrl.Bind(wx.EVT_COMBOBOX, self.on_auto_preview_change)
            elif isinstance(ctrl, wx.TextCtrl):
                ctrl.Bind(wx.EVT_TEXT, self.on_auto_preview_change)

    def _auto_preview_controls(self) -> list[wx.Window]:
        controls: list[wx.Window] = []
        controls.extend(self.defaults_panel.controls.values())
        controls.extend(self.defaults_panel.feature_panel.controls.values())
        controls.append(self.server_editor.name_ctrl)
        controls.extend(self.server_editor.controls.values())
        controls.extend(self.server_editor.feature_panel.controls.values())
        for panel in (self.defaults_panel.feature_panel, self.server_editor.feature_panel):
            if panel.enable_box is not None:
                controls.append(panel.enable_box)
        return controls

    def load_document(self, document: ConfigDocument) -> None:
        self._loading_document = True
        try:
            self.document = document
            self.defaults_panel.load_values(document.defaults, document.feature_defaults)
            self.server_list.Set([server.name for server in document.servers])
            if document.servers:
                self.current_server_index = 0
                self.server_list.SetSelection(0)
                self.server_editor.load_server(document.servers[0])
            else:
                self.current_server_index = None
                self.server_editor.load_server(None)
            self.update_server_actions()
        finally:
            self._loading_document = False

    def set_initial_focus(self) -> None:
        self.defaults_panel.focus_first_control()

    def update_server_actions(self) -> None:
        has_selection = self.server_list.GetSelection() != wx.NOT_FOUND and bool(self.document.servers)
        self.remove_server_btn.Enable(has_selection)
        self.editor_hint.Show(not has_selection)
        self.editor_hint.GetContainingSizer().Layout()

    def on_auto_preview_change(self, event) -> None:
        if event is not None:
            event.Skip()
        self.schedule_preview_update()

    def schedule_preview_update(self) -> None:
        if self._loading_document or self._closing:
            return
        if self._preview_update_pending:
            return
        self._preview_update_pending = True
        self._preview_update_timer = wx.CallLater(150, self.run_scheduled_preview_update)

    def run_scheduled_preview_update(self) -> None:
        self._preview_update_pending = False
        self._preview_update_timer = None
        if self._closing:
            return
        self.update_preview()

    def load_initial_document(self, config_path: Path | None) -> str:
        if config_path is None:
            self.load_document(_sample_document())
            return f"No {CONFIG_FILE_NAME} found; started with sample values."

        try:
            document = parse_document(config_path)
        except Exception as error:
            wx.MessageBox(str(error), "Open failed", wx.OK | wx.ICON_ERROR, self)
            self.load_document(_sample_document())
            return f"Could not open {config_path}; started with sample values."

        self.path = config_path
        self.load_document(document)
        return f"Opened {config_path}"

    def collect_document(self) -> ConfigDocument:
        self.save_current_server()
        defaults, feature_defaults = self.defaults_panel.get_defaults()
        self.document.defaults = defaults
        self.document.feature_defaults = feature_defaults
        return self.document

    def save_current_server(self) -> None:
        if self.current_server_index is None:
            return
        if self.current_server_index >= len(self.document.servers):
            return
        server = self.server_editor.get_server()
        self.document.servers[self.current_server_index] = server
        self.server_list.SetString(self.current_server_index, server.name or "(unnamed)")
        self.update_server_actions()

    def update_preview(self) -> bool:
        try:
            text = render_document(self.collect_document())
        except ValueError as error:
            self.preview_ctrl.SetValue(str(error))
            self.SetStatusText("Configuration has validation errors.")
            return False
        self.preview_ctrl.SetValue(text)
        self.SetStatusText("Preview updated.")
        return True

    def on_new(self, event) -> None:
        self.path = None
        self.load_document(ConfigDocument())
        self.update_preview()
        self.add_server_btn.SetFocus()

    def on_open(self, event) -> None:
        with wx.FileDialog(
            self,
            "Open ttcom.conf",
            wildcard="PowerCom config (*.conf)|*.conf|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = Path(dialog.GetPath())
        try:
            self.load_document(parse_document(path))
        except Exception as error:
            wx.MessageBox(str(error), "Open failed", wx.OK | wx.ICON_ERROR, self)
            return
        self.path = path
        self.update_preview()
        self.SetStatusText(f"Opened {path}")
        if self.document.servers:
            self.notebook.SetSelection(1)
            self.server_list.SetFocus()
        else:
            self.add_server_btn.SetFocus()

    def on_save(self, event) -> None:
        if self.path is None:
            self.on_save_as(event)
            return
        self.save_to_path(self.path)

    def on_save_as(self, event) -> None:
        with wx.FileDialog(
            self,
            "Save ttcom.conf",
            defaultFile="ttcom.conf",
            wildcard="PowerCom config (*.conf)|*.conf|All files (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            self.path = Path(dialog.GetPath())
        self.save_to_path(self.path)

    def save_to_path(self, path: Path) -> None:
        document = self.collect_document()
        errors = validate_document(document)
        if errors:
            wx.MessageBox("\n".join(errors), "Cannot save", wx.OK | wx.ICON_ERROR, self)
            return
        save_document(document, path)
        self.update_preview()
        self.SetStatusText(f"Saved {path}")

    def on_refresh_preview(self, event) -> None:
        if not self.update_preview():
            wx.MessageBox(
                self.preview_ctrl.GetValue() or "Configuration has validation errors.",
                "Preview validation errors",
                wx.OK | wx.ICON_WARNING,
                self,
            )

    def on_add_server(self, event) -> None:
        self.save_current_server()
        number = len(self.document.servers) + 1
        server = ServerConfig(f"server{number}", {"host": "", "tcpport": "10333"})
        self.document.servers.append(server)
        self.server_list.Append(server.name)
        self.current_server_index = len(self.document.servers) - 1
        self.server_list.SetSelection(self.current_server_index)
        self._loading_document = True
        try:
            self.server_editor.load_server(server)
        finally:
            self._loading_document = False
        self.update_server_actions()
        self.update_preview()
        self.SetStatusText(f"Added {server.name}. Edit the short name.")
        self.server_editor.focus_short_name()

    def on_remove_server(self, event) -> None:
        selection = self.server_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        removed_name = self.document.servers[selection].name or "(unnamed)"
        self.document.servers.pop(selection)
        self.server_list.Delete(selection)
        if self.document.servers:
            new_selection = min(selection, len(self.document.servers) - 1)
            self.current_server_index = new_selection
            self.server_list.SetSelection(new_selection)
            self.server_editor.load_server(self.document.servers[new_selection])
            self.server_list.SetFocus()
        else:
            self.current_server_index = None
            self.server_editor.load_server(None)
            self.add_server_btn.SetFocus()
        self.update_server_actions()
        self.update_preview()
        remaining = len(self.document.servers)
        if remaining == 0:
            self.SetStatusText(f"Removed {removed_name}. No servers remain; use Add server to create one.")
        else:
            self.SetStatusText(f"Removed {removed_name}. {remaining} servers remain.")

    def on_server_selected(self, event) -> None:
        old_index = self.current_server_index
        new_index = self.server_list.GetSelection()
        if old_index is not None and old_index != new_index:
            self.save_current_server()
        self.current_server_index = new_index if new_index != wx.NOT_FOUND else None
        if self.current_server_index is not None:
            self._loading_document = True
            try:
                self.server_editor.load_server(self.document.servers[self.current_server_index])
            finally:
                self._loading_document = False
            self.update_preview()
            self.SetStatusText(f"Editing server {self.document.servers[self.current_server_index].name}.")
        self.update_server_actions()

    def on_show(self, event) -> None:
        if event.IsShown() and not self._initial_focus_set:
            self._initial_focus_set = True
            wx.CallAfter(self.set_initial_focus)
        event.Skip()

    def on_exit(self, event) -> None:
        self._closing = True
        if self._preview_update_timer is not None:
            self._preview_update_timer.Stop()
            self._preview_update_timer = None
        self.Destroy()


def run(argv: list[str] | None = None) -> None:
    app = wx.App(False)
    frame = ConfigFrame(resolve_config_path(argv))
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    run(sys.argv[1:])
