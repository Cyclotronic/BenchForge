"""
Windows Desktop User Interface (`gui_qt.py`)

PySide6 desktop UI for BenchForge Studio. The application chrome is in the
professional Fluent register — menu bar, grouped toolbar, navigation rail,
banded data grids and a multi-pane status bar — while the geometry stays
soft: rounded panels, generous rows, and a lit status indicator.

All visual tokens come from `theme.py`; this module sets no colours.

Contains Adapter Controls, Profile Presets, Hardware Protocol Validation
Harness, TC Device Parser, TC Settings Manager, Performance Tester, and the
Live Traffic Snoop Debugger.
"""

import os
import queue
import sys
import threading
import time
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QPointF, QSize, QTimer, QSettings
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QKeySequence, QLinearGradient,
    QPainter, QPen, QPolygonF
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget, QListWidgetItem, QTextEdit, QSpinBox, QSlider, QFrame,
    QSplitter, QFileDialog, QMessageBox, QToolBar, QGroupBox, QAbstractItemView,
    QSizePolicy, QGraphicsDropShadowEffect, QCheckBox
)

from . import theme

from .diagnostics import format_record
from .prologix_emulator import PrologixEmulatorServer
from .version import __version__
from .vxi11_emulator import VXI11EmulatorServer


def slot_for(name: str, fallback: int) -> int:
    """
    GPIB address of a bench instrument by name.

    The bench gets rewired; hard-coded addresses then point at whatever moved
    into the slot, which is how a counter once came up configured as a DMM.
    """
    for spec in DEFAULT_BENCH:
        if spec["name"] == name:
            return int(spec["slot"])
    return fallback
from .ar488_emulator import AR488EmulatorServer
from .vxi11_lxi_emulator import LXIRawSocketServer, LXIDiscoveryResponder
from .device_emulator import (
    InstrumentRegistry, VirtualInstrument, DEFAULT_BENCH, build_instrument
)
from .tc_parser import generate_recommended_configs, TCDeviceDefinition


class Sparkline(QWidget):
    """
    Compact throughput history.

    A row of scalars tells you the present; the shape tells you whether traffic
    is steady, bursty, or has stopped — which is what you actually want while
    watching a client work.
    """

    def __init__(self, tokens: Dict[str, str], capacity: int = 120, parent=None):
        super().__init__(parent)
        self.tokens = tokens
        self.capacity = capacity
        self.samples: List[float] = []
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_tokens(self, tokens: Dict[str, str]):
        self.tokens = tokens
        self.update()

    def push(self, value: float):
        self.samples.append(max(0.0, float(value)))
        if len(self.samples) > self.capacity:
            del self.samples[: len(self.samples) - self.capacity]
        self.update()

    def clear(self):
        self.samples.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(1, 4, -1, -4)
        accent = QColor(self.tokens["accent"])
        line_col = QColor(self.tokens["lineSoft"])

        # Baseline, so an empty feed still reads as "zero" rather than "broken".
        pen = QPen(line_col, 1)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        if len(self.samples) < 2:
            painter.end()
            return

        peak = max(self.samples) or 1.0
        step = rect.width() / float(self.capacity - 1)
        offset = rect.width() - step * (len(self.samples) - 1)

        points = []
        for i, value in enumerate(self.samples):
            x = rect.left() + offset + i * step
            y = rect.bottom() - (value / peak) * (rect.height() - 2)
            points.append(QPointF(x, y))

        # Filled area under the trace.
        area = QPolygonF(points + [
            QPointF(points[-1].x(), rect.bottom()),
            QPointF(points[0].x(), rect.bottom()),
        ])
        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        fill_top = QColor(accent)
        fill_top.setAlpha(70)
        fill_bottom = QColor(accent)
        fill_bottom.setAlpha(0)
        gradient.setColorAt(0.0, fill_top)
        gradient.setColorAt(1.0, fill_bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPolygon(area)

        # Trace.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 1.4))
        painter.drawPolyline(QPolygonF(points))

        # Emphasised endpoint — the current value.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(points[-1], 2.6, 2.6)

        painter.end()


class MetricTile(QFrame):
    """One telemetry reading: an uppercase key, a monospaced value, a unit."""

    def __init__(self, key: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setFrameShape(QFrame.Shape.NoFrame)

        box = QVBoxLayout(self)
        box.setContentsMargins(13, 9, 13, 9)
        box.setSpacing(2)

        self.key_label = QLabel(key.upper())
        self.key_label.setProperty("role", "tileKey")

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(4)

        self.value_label = QLabel("0")
        self.value_label.setProperty("role", "tileValue")
        self.unit_label = QLabel(unit)
        self.unit_label.setProperty("role", "tileUnit")

        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label, alignment=Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch()

        box.addWidget(self.key_label)
        box.addLayout(value_row)

    def set_value(self, text: str, good: bool = False):
        self.value_label.setText(text)
        role = "tileValueGood" if good else "tileValue"
        if self.value_label.property("role") != role:
            self.value_label.setProperty("role", role)
            self.value_label.style().unpolish(self.value_label)
            self.value_label.style().polish(self.value_label)


class BenchForgeQtApp(QMainWindow):
    """Main window for BenchForge Studio."""

    def __init__(self):
        super().__init__()

        # The version belongs in the title bar: a tester reporting a problem
        # can read it off a screenshot, and the packaged app is windowed so the
        # startup banner is never seen.
        self.setWindowTitle(
            "BenchForge Studio %s - Universal Bench Instrument & Gateway Emulator"
            % __version__)
        self.resize(1180, 840)
        self.setMinimumSize(1000, 680)

        # Initialize Backend Components
        self.registry = InstrumentRegistry()
        self.server = PrologixEmulatorServer(
            host="127.0.0.1", port=1234, registry=self.registry, connection_policy=PrologixEmulatorServer.POLICY_SINGLE
        )
        self.server.add_packet_callback(self._on_packet_event_callback)
        self.server.add_diagnostic_callback(self._on_diagnostic_callback)

        self.lxi_raw_server = LXIRawSocketServer(host="127.0.0.1", port=5025, registry=self.registry)

        # The E5810A's real client interface. Kept alongside rather than
        # swapped in by _set_gateway_class, because it is a different protocol
        # on different ports, not another flavour of the ++ command set.
        self.vxi11_server = VXI11EmulatorServer(host="127.0.0.1",
                                                registry=self.registry)
        self.vxi11_server.add_packet_callback(self._on_packet_event_callback)
        self.vxi11_server.add_diagnostic_callback(self._on_diagnostic_callback)
        self.lxi_discovery = LXIDiscoveryResponder(model_name="Keysight 34461A")

        self.parsed_devices: Dict[str, TCDeviceDefinition] = {}
        self.snoop_paused = False
        self.snoop_queue = queue.Queue(maxsize=5000)
        self.warning_queue = queue.Queue(maxsize=2000)
        self.warning_count = 0
        self.error_count = 0
        self.debug_records: List[Dict[str, Any]] = []
        self.total_packet_count = 0
        # Written from server threads on every packet, read and rebuilt by the
        # 50 ms GUI timer. sum() over a list another thread is appending to can
        # raise, and the rebuild at _refresh_telemetry can drop samples, so both
        # sides take this lock.
        self.qps_window = []
        self.latency_window = []
        self._telemetry_lock = threading.Lock()
        self._previous_preset = "Prologix Ethernet (Official v01.06.06.00)"

        # Load Persistent Application Preferences via QSettings
        self.settings = QSettings("BenchForge", "Studio")

        # Apply the Fluent stylesheet before widgets are built.
        self._apply_qt_stylesheet()

        # Build UI Components
        self._init_ui()

        # Sync Default Mode State & Restore Session Settings
        self._restore_session_settings()

        # Drain queued packets onto the grid in batches (see _drain_snoop_queue).
        self.snoop_timer = QTimer(self)
        self.snoop_timer.setInterval(50)
        self.snoop_timer.timeout.connect(self._drain_snoop_queue)
        self.snoop_timer.timeout.connect(self._drain_warning_queue)
        self.snoop_timer.start()

        # Auto-start engine on launch if preference is enabled.
        # Read through _setting so BENCHFORGE_IGNORE_SETTINGS really means
        # "defaults": reading QSettings directly here let a saved
        # auto_start_engine=false leak into a run that was supposed to be
        # clean, and the engine silently never started.
        if self._setting("auto_start_engine", True, bool):
            QTimer.singleShot(0, self._autostart_engine)

    #: Set BENCHFORGE_IGNORE_SETTINGS=1 to start from defaults and persist
    #: nothing. Release verification needs this: the packaged app otherwise
    #: inherits whatever mode and port the developer last used, so the same
    #: build passes on one machine and fails on another for reasons that have
    #: nothing to do with the build.
    IGNORE_SETTINGS_ENV = "BENCHFORGE_IGNORE_SETTINGS"

    @classmethod
    def _settings_ignored(cls) -> bool:
        return bool(os.environ.get(cls.IGNORE_SETTINGS_ENV))

    def _setting(self, key, default, value_type=None):
        """
        Read a persisted preference, honouring the clean-room switch.

        Every read must go through here. A direct self.settings.value() call
        bypasses BENCHFORGE_IGNORE_SETTINGS, which is how a saved
        auto_start_engine=false reached a run that was meant to start from
        defaults -- the engine never started and the packaged build looked
        broken when it was behaving exactly as configured.
        """
        if self._settings_ignored():
            return default
        if value_type is not None:
            return self.settings.value(key, default, type=value_type)
        return self.settings.value(key, default)

    def _restore_session_settings(self):
        if self._settings_ignored():
            # Every showMessage carries a timeout. An untimed message stays on
            # screen until something else replaces it, so the bar ends up
            # showing whatever happened to be last rather than what is true now.
            self.statusBar().showMessage(
                "Started from defaults (%s set); settings will not be saved."
                % self.IGNORE_SETTINGS_ENV, 6000)
            self._on_mode_changed(0)
            return

        saved_mode = self.settings.value("last_emulation_mode", "")
        saved_host = self.settings.value("last_host", "127.0.0.1")
        saved_port = self.settings.value("last_port", "")
        saved_delay = self.settings.value("last_query_delay", -1, type=int)

        # setCurrentIndex only emits currentIndexChanged when the index actually
        # moves, so restoring the mode that is already selected would leave the
        # device table empty. Apply the mode explicitly instead of relying on
        # the signal.
        idx = 0
        if saved_mode and hasattr(self, "mode_cb"):
            found = self.mode_cb.findText(saved_mode)
            if found >= 0:
                idx = found
        if hasattr(self, "mode_cb") and self.mode_cb.currentIndex() != idx:
            self.mode_cb.blockSignals(True)
            self.mode_cb.setCurrentIndex(idx)
            self.mode_cb.blockSignals(False)
        self._on_mode_changed(idx)

        if saved_host and hasattr(self, "host_input"):
            self.host_input.setText(saved_host)
        if saved_delay >= 0 and hasattr(self, "delay_slider"):
            self.delay_slider.setValue(saved_delay)
        if saved_port:
            if hasattr(self, "port_input") and self.port_input.isVisible():
                self.port_input.setText(saved_port)
            elif hasattr(self, "lxi_port_input") and self.lxi_port_input.isVisible():
                self.lxi_port_input.setText(saved_port)
        saved_tc_path = self.settings.value("tc_path", "")
        if saved_tc_path and hasattr(self, "tc_path_input"):
            self.tc_path_input.setText(saved_tc_path)

    def _autostart_engine(self):
        self._start_servers(silent=True)
        if self.server._is_running or self.lxi_raw_server._is_running:
            self.statusBar().showMessage("Engine started automatically.", 4000)

    def _apply_qt_stylesheet(self):
        """Applies the Fluent stylesheet, following the operating system colour scheme."""
        self.palette_tokens = theme.palette_for_scheme()
        self.setStyleSheet(theme.build_qss(self.palette_tokens))

        # Re-theme live when the user flips Windows between light and dark.
        try:
            hints = QApplication.instance().styleHints()
            hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        except Exception:
            pass

    def _on_color_scheme_changed(self, *_):
        self.palette_tokens = theme.palette_for_scheme()
        self.setStyleSheet(theme.build_qss(self.palette_tokens))
        self._refresh_status_led()
        if hasattr(self, "telem_spark"):
            self.telem_spark.set_tokens(self.palette_tokens)

    # ------------------------------------------------------------------
    # Chrome: menu bar, toolbar, navigation rail, status bar
    # ------------------------------------------------------------------

    NAV_ITEMS = [
        ("group", "EMULATE"),
        ("page", "Adapter & Presets"),
        ("page", "Virtual Instruments"),
        ("page", "TestController Settings"),
        ("group", "INSPECT"),
        ("page", "Traffic Snoop"),
    ]

    def _init_ui(self):
        self._build_actions()
        self._build_menubar()
        self._build_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Navigation rail replaces the former tab bar.
        self.nav = QListWidget()
        self.nav.setObjectName("NavRail")
        self.nav.setFixedWidth(theme.RAIL_WIDTH)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.currentRowChanged.connect(self._on_nav_changed)

        self.stack = QStackedWidget()

        self.tab_adapter = QWidget()
        self.tab_parser = QWidget()
        self.tab_config = QWidget()
        self.tab_snoop = QWidget()
        pages = [self.tab_adapter, self.tab_parser, self.tab_config, self.tab_snoop]

        page_idx = 0
        self._nav_row_for_page = {}
        for kind, label in self.NAV_ITEMS:
            item = QListWidgetItem(label)
            if kind == "group":
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                wrapper = QWidget()
                wrap = QVBoxLayout(wrapper)
                wrap.setContentsMargins(12, 12, 12, 12)
                wrap.setSpacing(10)
                wrap.addWidget(pages[page_idx])
                self.stack.addWidget(wrapper)
                self._nav_row_for_page[page_idx] = self.nav.count()
                item.setData(Qt.ItemDataRole.UserRole, page_idx)
                page_idx += 1
            self.nav.addItem(item)

        root.addWidget(self.nav)
        root.addWidget(self.stack, stretch=1)

        self._init_tab_adapter()
        self._init_tab_parser()
        self._init_tab_config()
        self._init_tab_snoop()

        self._build_statusbar()
        self.nav.setCurrentRow(self._nav_row_for_page[0])

    def _build_actions(self):
        """Actions are shared by the menu bar and toolbar so enabled state stays in sync."""
        self.act_start = QAction("&Start Engine", self)
        self.act_start.setShortcut(QKeySequence("F5"))
        self.act_start.setStatusTip("Start all emulator socket listeners")
        # QAction.triggered carries a `checked` bool; swallow it so it can never
        # be mistaken for the `silent` argument.
        self.act_start.triggered.connect(lambda _checked=False: self._start_servers())

        self.act_stop = QAction("Sto&p Engine", self)
        self.act_stop.setShortcut(QKeySequence("Shift+F5"))
        self.act_stop.setStatusTip("Stop all emulator socket listeners")
        self.act_stop.setEnabled(False)
        self.act_stop.triggered.connect(self._stop_servers)

        self.act_restart = QAction("&Restart Engine", self)
        self.act_restart.setEnabled(False)
        self.act_restart.triggered.connect(self._restart_servers)

        self.act_gen_cfg = QAction("&Generate TestController Configs", self)
        self.act_gen_cfg.triggered.connect(self._generate_configs)

        self.act_quit = QAction("E&xit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)

        self.act_pause_snoop = QAction("&Pause Traffic Feed", self)
        self.act_pause_snoop.setCheckable(True)
        self.act_pause_snoop.triggered.connect(self._toggle_snoop_pause)

        self.act_clear_snoop = QAction("&Clear Traffic Feed", self)
        self.act_clear_snoop.triggered.connect(self._clear_snoop)

        self.act_auto_start = QAction("⚡ Auto-Start Engine on Launch", self)
        self.act_auto_start.setCheckable(True)
        self.act_auto_start.setChecked(self._setting("auto_start_engine", True, bool))
        self.act_auto_start.triggered.connect(self._on_autostart_toggled)

        self.act_about = QAction("&About BenchForge Studio", self)
        self.act_about.triggered.connect(self._show_about)

    def _build_menubar(self):
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_gen_cfg)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_clear_snoop)

        m_view = mb.addMenu("&View")
        page_labels = [label for kind, label in self.NAV_ITEMS if kind == "page"]
        for idx, label in enumerate(page_labels):
            act = QAction(label, self)
            act.setShortcut(QKeySequence("Ctrl+%d" % (idx + 1)))
            act.triggered.connect(lambda _checked=False, i=idx: self._goto_page(i))
            m_view.addAction(act)
        m_view.addSeparator()
        m_view.addAction(self.act_pause_snoop)

        m_settings = mb.addMenu("&Settings")
        m_settings.addAction(self.act_auto_start)

        m_help = mb.addMenu("&Help")
        m_help.addAction(self.act_about)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        self.toolbar = tb

        # Group 1 - lifecycle
        self.btn_start = QPushButton("Start Engine")
        self.btn_start.setProperty("kind", "accent")
        self.btn_start.clicked.connect(self.act_start.trigger)
        tb.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Stop Engine")
        self.btn_stop.setProperty("kind", "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.act_stop.trigger)
        tb.addWidget(self.btn_stop)

        self.btn_restart = QPushButton("Restart Engine")
        self.btn_restart.setEnabled(False)
        self.btn_restart.clicked.connect(self.act_restart.trigger)
        tb.addWidget(self.btn_restart)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # Status indicator
        self.status_led = QLabel()
        self.status_led.setObjectName("StatusLed")
        tb.addWidget(self.status_led)
        self._refresh_status_led()

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)

        self.sb_state = QLabel("Ready")
        self.sb_profile = QLabel("Prologix Ethernet v01.06.06.00")
        self.sb_clients = QLabel("Clients 0")
        self.sb_bind = QLabel("127.0.0.1")
        self.sb_bind.setProperty("role", "statusEnd")

        # ALL of these are permanent widgets, and that matters.
        #
        # QStatusBar hides every widget added with addWidget() for as long as a
        # temporary showMessage() is displayed, and restores them afterwards.
        # State, profile and policy used to be added that way, so they vanished
        # and reappeared as messages came and went -- with several messages
        # carrying no timeout, they could stay hidden indefinitely. That is the
        # intermittent "overwritten status bar".
        #
        # Permanent widgets are never obscured, which leaves the left-hand area
        # free for showMessage to use as it is designed to.
        for widget in (self.sb_state, self.sb_profile, self.sb_clients, self.sb_bind):
            sb.addPermanentWidget(widget)

    @staticmethod
    def _set_label(label: QLabel, text: str):
        """
        Update a status label only when the text actually changes.

        _refresh_telemetry runs on the 50 ms drain tick, so an unguarded
        setText rewrote the status bar twenty times a second whether or not
        anything had moved. Each write asks Qt to re-lay-out the bar, which is
        both wasted work and a plausible source of repaint artefacts.
        """
        if label.text() != text:
            label.setText(text)

    def _refresh_status_led(self):
        mode = self.mode_cb.currentText() if hasattr(self, "mode_cb") else ""
        is_e5810a = "Keysight E5810A" in mode
        running = self.lxi_raw_server._is_running if is_e5810a else self.server._is_running

        if running:
            host = self.host_input.text() if hasattr(self, "host_input") else "127.0.0.1"
            if is_e5810a:
                port = self.lxi_port_input.text() if hasattr(self, "lxi_port_input") else "5025"
                self.status_led.setText(f"\u25cf  Running   {host}:{port} (LXI)")
            else:
                port = self.port_input.text() if hasattr(self, "port_input") else "1234"
                self.status_led.setText(f"\u25cf  Running   {host}:{port} (Gateway)")
            self.status_led.setProperty("state", "running")
        else:
            self.status_led.setText("\u25cf  Stopped")
            self.status_led.setProperty("state", "stopped")

        # Property-driven QSS selectors require an explicit repolish.
        self.status_led.style().unpolish(self.status_led)
        self.status_led.style().polish(self.status_led)

        # QSS cannot express a glow, so the lit state gets a real drop shadow.
        if running:
            glow = QGraphicsDropShadowEffect(self.status_led)
            glow.setBlurRadius(theme.LED_GLOW_RADIUS)
            glow.setOffset(0, 0)
            colour = QColor(self.palette_tokens["good"])
            colour.setAlpha(170)
            glow.setColor(colour)
            self.status_led.setGraphicsEffect(glow)
        else:
            self.status_led.setGraphicsEffect(None)

    def _goto_page(self, page_idx: int):
        self.nav.setCurrentRow(self._nav_row_for_page[page_idx])

    def _on_nav_changed(self, row: int):
        item = self.nav.item(row)
        if item is None:
            return
        page_idx = item.data(Qt.ItemDataRole.UserRole)
        if page_idx is not None:
            self.stack.setCurrentIndex(page_idx)

    def _show_about(self):
        QMessageBox.about(
            self,
            "About BenchForge Studio",
            "<b>BenchForge Studio</b><br>"
            "Universal Bench Instrument &amp; Gateway Emulator Suite<br><br>"
            "Emulates Prologix Ethernet (1234), LXI SCPI raw socket (5025) "
            "and LXI mDNS discovery (5353).<br><br>"
            "MIT licensed. Built with Qt for Python (PySide6, LGPL v3).",
        )

    @staticmethod
    def _style_table(table: QTableWidget):
        """Shared data-grid conventions: banded rows, compact, no grid lines."""
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(True)

    # --- TAB 1: ADAPTER CONTROL & PRESETS ---
    def _init_tab_adapter(self):
        layout = QVBoxLayout(self.tab_adapter)
        layout.setSpacing(12)

        # Top Card: Active Emulator
        top_box = QGroupBox("Active Emulator")
        top_layout = QGridLayout(top_box)
        top_layout.setContentsMargins(12, 12, 12, 12)
        top_layout.setSpacing(12)

        # Row 0: Emulator Persona & Binding Host
        top_layout.addWidget(QLabel("Emulator Persona:"), 0, 0)
        self.mode_cb = QComboBox()
        self.mode_cb.addItems([
            "Prologix Ethernet Gateway (v01.06.06.00)",
            "Keysight E5810A LAN/GPIB Gateway",
            "AR488 / AR488Lan GPIB Gateway",
        ])
        self.mode_cb.setMinimumWidth(320)
        self.mode_cb.currentIndexChanged.connect(self._on_mode_changed)
        top_layout.addWidget(self.mode_cb, 0, 1)

        top_layout.addWidget(QLabel("Binding Host:"), 0, 2)
        self.host_input = QLineEdit("127.0.0.1")
        self.host_input.setMaximumWidth(150)
        top_layout.addWidget(self.host_input, 0, 3)

        # Row 1: Ports & Query Delay
        self.port_lbl = QLabel("Gateway Port:")
        top_layout.addWidget(self.port_lbl, 1, 0)
        self.port_input = QLineEdit("1234")
        self.port_input.setMaximumWidth(120)
        top_layout.addWidget(self.port_input, 1, 1)

        self.lxi_port_lbl = QLabel("LXI SCPI Port:")
        top_layout.addWidget(self.lxi_port_lbl, 1, 0)
        self.lxi_port_input = QLineEdit("5025")
        self.lxi_port_input.setMaximumWidth(120)
        top_layout.addWidget(self.lxi_port_input, 1, 1)
        self.lxi_port_lbl.hide()
        self.lxi_port_input.hide()

        top_layout.addWidget(QLabel("Socket Query Delay (ms):"), 1, 2)
        delay_box = QHBoxLayout()
        self.delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.delay_slider.setRange(0, 500)
        self.delay_slider.setValue(0)
        self.delay_slider.setToolTip("Simulates GPIB bus transmission latency on query responses.")
        self.delay_lbl = QLabel("0 ms")
        self.delay_lbl.setProperty("role", "fieldLabel")
        self.delay_slider.valueChanged.connect(self._update_impairments)
        delay_box.addWidget(self.delay_slider)
        delay_box.addWidget(self.delay_lbl)
        top_layout.addLayout(delay_box, 1, 3)

        layout.addWidget(top_box)

        # Main Card Below: Active Virtual Instruments
        main_box = QGroupBox("Active Virtual Instruments")
        main_layout = QVBoxLayout(main_box)
        main_layout.setContentsMargins(12, 12, 12, 12)

        self.device_table = QTableWidget(0, 3)
        self._style_table(self.device_table)
        self.device_table.setHorizontalHeaderLabels(["GPIB Slot", "Instrument Model", "Hardware *IDN? Response"])
        self.device_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.device_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.device_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        main_layout.addWidget(self.device_table)
        layout.addWidget(main_box, stretch=1)

    # --- TAB 2: VIRTUAL INSTRUMENT LIBRARY ---
    PREBUILT_LIBRARY = {
        "Agilent 34401A 6½-Digit DMM": {
            "name": "Agilent 34401A",
            "idn": "HEWLETT-PACKARD,34401A,0,10-1-1",
            "description": "6½-digit precision multimeter with DC/AC voltage, current, 2/4-wire resistance, frequency, and continuity modes.",
            "commands": [
                "*IDN? -> HEWLETT-PACKARD,34401A,0,10-1-1",
                "*RST  -> Reset instrument state",
                ":MEAS:VOLT:DC? -> Read DC Voltage",
                ":MEAS:VOLT:AC? -> Read AC Voltage",
                ":MEAS:RES? -> Read Resistance (2-wire)",
                ":MEAS:FRES? -> Read Resistance (4-wire)",
                ":READ? -> Trigger and fetch measurement",
                ":FETC? -> Fetch buffered measurement",
            ],
        },
        "Fluke PM6690 Frequency Counter": {
            "name": "Fluke PM6690",
            "class": "COUNTER",
            "idn": "FLUKE, PM6690, 979819, V1.32 26 May 2022 09:54",
            "bench": 4,
            "description": "High-resolution frequency counter/timer with dual channel inputs. Readings express a fixed 0.01 Hz resolution, so the decimal count moves with the decade.",
            "commands": [
                "FUNC?  -> \"FREQ 1\"   (function AND input channel)",
                "CONF?  -> \"FREQ 1\"   (identical to FUNC? on a counter)",
                ":READ? -> +9.99999962E+06",
                ":FETC? -> +1.000000038E+07   (same value, 0.01 Hz either way)",
                "*OPT?  -> Option 30, Option 10, 0",
            ],
        },
        "HP E3631A Triple-Output Power Supply": {
            "name": "HP E3631A",
            "class": "PSU",
            "bench": 7,
            "tc_driver": False,
            "idn": "HEWLETT-PACKARD,E3631A,0,2.1-5.0-1.0",
            "description": "Triple-output supply (+6 V, +25 V, -25 V). NOTE: TestController ships no driver for this model -- AgilentHP E363xA.TXT covers the E3632A/E3633A/E3634A only. Assigning it will emulate correctly on the bus, but the client will not be able to load it.",
            "commands": [
                "INST?      -> P6V",
                "VOLT?      -> +0.00000000E+00   (setpoint)",
                "MEAS:VOLT? -> -4.81193600E-01   (meter, NOT the setpoint)",
                "APPL?      -> \"0.000000,5.000000\"   (quoted, unlike the rest)",
                "VOLT:PROT? -> <silent; this model has no OVP subsystem>",
            ],
        },
        "Keithley 2010 Low-Noise DMM": {
            "name": "Keithley 2010",
            "class": "DMM",
            "bench": 3,
            "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A10  /A02  ",
            "description": "Ultra-low noise 7½-digit multimeter designed for precision resistance and voltage measurements.",
            "commands": [
                "FUNC?  -> \"VOLT:DC\"   (long-form spelling, unlike Agilent)",
                "CONF?  -> \"VOLT:DC\"   (no range/resolution pair)",
                ":READ? -> +1.00001363E+01",
                ":MEAS:FRES? -> Read 4-wire Resistance",
            ],
        },
        "Keithley 2001M High-Performance DMM": {
            "name": "Keithley 2001M",
            "class": "DMM",
            "bench": 2,
            "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2001M,1150952,B16  /A02  ",
            "description": "7½-digit high-resolution multimeter. Returns multi-element readings: value, timestamp, reading number and channel.",
            "commands": [
                "FUNC?  -> \"VOLT:DC\"",
                ":READ? -> +10.00005E+00NVDC,+20598.693324SECS,"
                "+69831RDNG#,00EXTCHAN",
            ],
        },
        "Keithley 2002 High-Performance DMM": {
            "name": "Keithley 2002",
            "class": "DMM",
            "bench": 1,
            "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2002,4461274,B02  /A02  ",
            "description": "8½-digit multimeter. Multi-element readings like the 2001M, but six decimals rather than five.",
            "commands": [
                "*OPT?  -> MEM2,0",
                "FUNC?  -> \"VOLT:DC\"",
                ":READ? -> +10.000086E+00NVDC,+20565.811885SECS,"
                "+40709RDNG#,00EXTCHAN",
            ],
        },
        "Agilent 34411A 6½-Digit DMM": {
            "name": "Agilent 34411A",
            "class": "DMM",
            "bench": 5,
            "idn": "Agilent Technologies,34411A,MY48005929,2.43-2.40-0.09-46-09",
            "description": "6½-digit high-speed multimeter. Reports the short function vocabulary and signs its integer replies.",
            "commands": [
                "FUNC?  -> \"VOLT\"      (short form, unlike Keithley)",
                "CONF?  -> \"VOLT <range>,<resolution>\"",
                ":READ? -> +2.28648288E+01",
                "*ESR?  -> +36          (signed, unlike Keithley)",
            ],
        },
        "Agilent 33250A Function Generator": {
            "name": "Agilent 33250A",
            "class": "FUNCGEN",
            "idn": "Agilent Technologies,33250A,0,2.04-1.01-2.00-03-2",
            "description": "80 MHz function / arbitrary waveform generator. Answers FUNC? with a bare waveform name and no quotes.",
            "commands": [
                "FUNC?      -> SIN     (UNQUOTED -- generators do not quote)",
                "FREQ?      -> +1.0000000000000E+03",
                "VOLT?      -> +1.0000000000000E-01",
                "OUTP?      -> 0",
                "*OPT?      -> <silent; logs -113 Undefined header>",
            ],
        },
        "Keysight 34461A Truevolt DMM": {
            "name": "Keysight 34461A",
            "idn": "Keysight Technologies,34461A,MY53206545,A.03.03-02.40-03.03-00.52-01-01",
            "description": "Industry standard 6½-digit Truevolt LXI digital multimeter.",
            "commands": [
                "*IDN? -> Keysight Technologies,34461A,MY53206545,A.03.03-02.40...",
                "*RST  -> Reset instrument",
                ":MEAS:VOLT:DC? -> Read DC Voltage",
                ":READ? -> Trigger and read measurement",
            ],
        },
        "Siglent SDM3065X 6½-Digit DMM": {
            "name": "Siglent SDM3065X",
            "idn": "Siglent Technologies,SDM3065X,SDM36XBD2R0112,01.01.01.20R1",
            "description": "6½-digit dual-display digital multimeter with LAN and USB interfaces.",
            "commands": [
                "*IDN? -> Siglent Technologies,SDM3065X,SDM36XBD2R0112,01.01.01.20R1",
                "*RST  -> Reset instrument",
                ":MEAS:VOLT:DC? -> Read DC Voltage",
            ],
        },
    }

    def _init_tab_parser(self):
        layout = QVBoxLayout(self.tab_parser)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: List of Prebuilt Instruments
        left_box = QGroupBox("Virtual Instrument Library")
        left_layout = QVBoxLayout(left_box)
        self.parser_list = QListWidget()
        for inst_title in self.PREBUILT_LIBRARY.keys():
            self.parser_list.addItem(inst_title)
        self.parser_list.itemSelectionChanged.connect(self._on_device_selected)
        left_layout.addWidget(self.parser_list)
        splitter.addWidget(left_box)

        # Right: Instrument Details & Slot Assignment
        right_box = QGroupBox("Instrument Specifications & Slot Assignment")
        right_layout = QVBoxLayout(right_box)
        self.parser_text = QTextEdit()
        self.parser_text.setReadOnly(True)
        right_layout.addWidget(self.parser_text)

        assign_box = QHBoxLayout()
        assign_box.addWidget(QLabel("Assign to GPIB Slot (0-30):"))
        self.assign_slot_spin = QSpinBox()
        self.assign_slot_spin.setRange(0, 30)
        self.assign_slot_spin.setValue(1)
        assign_box.addWidget(self.assign_slot_spin)

        btn_assign = QPushButton("⚡ Assign Instrument to Slot")
        btn_assign.setProperty("kind", "accent")
        btn_assign.clicked.connect(self._assign_prebuilt_device_to_slot)
        assign_box.addWidget(btn_assign)
        assign_box.addStretch()

        right_layout.addLayout(assign_box)
        splitter.addWidget(right_box)

        splitter.setSizes([320, 680])
        layout.addWidget(splitter, stretch=1)

    # --- TAB 3: TESTCONTROLLER SETTINGS GENERATOR ---
    def _init_tab_config(self):
        layout = QVBoxLayout(self.tab_config)

        # TestController Settings Folder & Auto-Deployment Box
        tc_box = QGroupBox("TestController Settings Folder")
        tc_layout = QVBoxLayout(tc_box)

        tc_path_bar = QHBoxLayout()
        tc_path_bar.addWidget(QLabel("TestController Settings Folder:"))
        self.tc_path_input = QLineEdit()
        self.tc_path_input.setPlaceholderText("e.g. C:\\TestController\\Settings or /Users/name/TestController/Settings")
        self.tc_path_input.setToolTip("Path to TestController's Settings folder where settingsGPIB.txt and settingsLoad.txt reside.")
        tc_path_bar.addWidget(self.tc_path_input, stretch=1)

        btn_browse_tc = QPushButton("Browse...")
        btn_browse_tc.setToolTip("Select TestController's Settings folder")
        btn_browse_tc.clicked.connect(self._browse_tc_path)
        tc_path_bar.addWidget(btn_browse_tc)

        self.btn_auto_config_tc = QPushButton("🚀 Auto-Configure TestController Files")
        self.btn_auto_config_tc.setProperty("kind", "accent")
        self.btn_auto_config_tc.setToolTip("Deploys settingsGPIB.txt and settingsLoad.txt directly into the TestController Settings folder.")
        self.btn_auto_config_tc.clicked.connect(self._auto_configure_tc)
        tc_path_bar.addWidget(self.btn_auto_config_tc)

        tc_layout.addLayout(tc_path_bar)
        layout.addWidget(tc_box)

        top_bar = QHBoxLayout()
        btn_gen = QPushButton("⚡ Refresh / Regenerate Preview")
        btn_gen.clicked.connect(self._generate_configs)
        top_bar.addWidget(btn_gen)
        top_bar.addStretch()

        layout.addLayout(top_bar)

        # Dual Code Blocks
        splitter = QSplitter(Qt.Orientation.Vertical)

        gpib_box = QGroupBox("settingsGPIB.txt")
        gpib_layout = QVBoxLayout(gpib_box)
        self.txt_gpib = QTextEdit()
        btn_exp_gpib = QPushButton("💾 Export settingsGPIB.txt")
        btn_exp_gpib.clicked.connect(lambda: self._export_file("settingsGPIB.txt", self.txt_gpib.toPlainText()))
        gpib_layout.addWidget(self.txt_gpib)
        gpib_layout.addWidget(btn_exp_gpib, alignment=Qt.AlignmentFlag.AlignRight)
        splitter.addWidget(gpib_box)

        load_box = QGroupBox("settingsLoad.txt")
        load_layout = QVBoxLayout(load_box)
        self.txt_load = QTextEdit()
        btn_exp_load = QPushButton("💾 Export settingsLoad.txt")
        btn_exp_load.clicked.connect(lambda: self._export_file("settingsLoad.txt", self.txt_load.toPlainText()))
        load_layout.addWidget(self.txt_load)
        load_layout.addWidget(btn_exp_load, alignment=Qt.AlignmentFlag.AlignRight)
        splitter.addWidget(load_box)

        layout.addWidget(splitter, stretch=1)

    # A validation/benchmark tab was removed here. It was left over from the
    # Tkinter migration and could not run: nothing called it, `self.tab_testing`
    # was never created, and both button handlers -- _run_validation_harness and
    # _run_benchmark -- did not exist. Wiring it up would have crashed on the
    # first line.
    #
    # The functionality itself is intact and tested from the command line:
    #     python tests/run_validation_harness.py            # 12 protocol checks
    #     python tests/run_validation_harness.py --benchmark # QPS stress
    # backed by core/validation_harness.py and core/performance_tester.py.
    # Building a proper UI for it means worker threads so the network calls do
    # not block the Qt main thread -- a feature, not a fix, and deliberately not
    # done at release-candidate stage.

    # --- TAB 4: TRAFFIC SNOOP & TELEMETRY ---
    def _init_tab_snoop(self):
        layout = QVBoxLayout(self.tab_snoop)
        layout.setSpacing(10)

        # Telemetry: four readings over a shared throughput trace.
        telem_panel = QFrame()
        telem_panel.setObjectName("Panel")
        telem_outer = QVBoxLayout(telem_panel)
        telem_outer.setContentsMargins(1, 1, 1, 10)
        telem_outer.setSpacing(0)

        # Hairline gaps between tiles, so the row reads as one instrument
        # cluster rather than four floating chips.
        tile_row = QHBoxLayout()
        tile_row.setContentsMargins(0, 0, 0, 0)
        tile_row.setSpacing(1)

        self.tile_throughput = MetricTile("Throughput", "q/s")
        self.tile_latency = MetricTile("Avg latency", "ms")
        self.tile_clients = MetricTile("Clients", "connected")
        self.tile_packets = MetricTile("Packets", "captured")

        for tile in (self.tile_throughput, self.tile_latency,
                     self.tile_clients, self.tile_packets):
            tile_row.addWidget(tile, stretch=1)

        telem_outer.addLayout(tile_row)

        self.telem_spark = Sparkline(self.palette_tokens)
        self.telem_spark.setToolTip("Queries per second over the last two minutes")
        telem_outer.addSpacing(6)
        telem_outer.addWidget(self.telem_spark)

        layout.addWidget(telem_panel)

        toolbar = QHBoxLayout()
        btn_pause = QPushButton("Pause feed")
        btn_pause.clicked.connect(self._toggle_snoop_pause)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear_snoop)

        toolbar.addWidget(btn_pause)
        toolbar.addWidget(btn_clear)
        btn_export_traffic = QPushButton("Export traffic…")
        btn_export_traffic.clicked.connect(self._export_traffic)
        toolbar.addWidget(btn_export_traffic)
        toolbar.addStretch()

        # Diagnostic counts are the reason to look at this page at all when
        # something is wrong, so keep them live next to the feed controls.
        self.warn_count_label = QLabel("Warnings: 0   Errors: 0")
        self.warn_count_label.setObjectName("WarnCount")
        toolbar.addWidget(self.warn_count_label)
        layout.addLayout(toolbar)

        # The wire and its interpretation are separate jobs, so they get
        # separate panes. A developer scanning raw traffic for a corrupted read
        # should not have to pick commentary out of the same list.
        self.snoop_split = QSplitter(Qt.Orientation.Vertical)

        self.snoop_table = QTableWidget(0, 6)
        self._style_table(self.snoop_table)
        self.snoop_table.setHorizontalHeaderLabels(["Timestamp", "Client Endpoint", "Dir", "GPIB Addr", "Traffic Content", "Latency (ms)"])
        self.snoop_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.snoop_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.snoop_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.snoop_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.snoop_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        # The payload is the reason this table exists; give it the slack rather
        # than letting it be elided to '+9.999999…' while columns of fixed-width
        # metadata sit comfortably.
        self.snoop_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)

        traffic_box = QGroupBox("Data Stream  —  what actually went over the wire")
        traffic_layout = QVBoxLayout(traffic_box)
        traffic_layout.setContentsMargins(8, 6, 8, 8)
        traffic_layout.addWidget(self.snoop_table)
        self.snoop_split.addWidget(traffic_box)

        # --- Debug log ------------------------------------------------------
        # The interpretation pane. Conditions real hardware records in its error
        # queue and the wire does not show -- a read issued with nothing
        # pending, or a query sent over an unread reply -- both look like plain
        # silence to the client, which is what makes them expensive to find.
        debug_box = QGroupBox("Debug Log  —  errors, diagnostics and lifecycle")
        debug_layout = QVBoxLayout(debug_box)
        debug_layout.setContentsMargins(8, 6, 8, 8)

        hint = QLabel(
            "A WARN line is not an emulator fault. It is a SCPI error a real "
            "instrument would have queued in response to the client's traffic.")
        hint.setWordWrap(True)
        hint.setObjectName("WarnHint")
        debug_layout.addWidget(hint)

        filters = QHBoxLayout()
        self.debug_filters = {}
        for level in ("INFO", "WARN", "ERROR"):
            box = QCheckBox(level)
            box.setChecked(True)
            box.stateChanged.connect(self._apply_debug_filter)
            self.debug_filters[level] = box
            filters.addWidget(box)
        filters.addStretch()

        btn_clear_debug = QPushButton("Clear log")
        btn_clear_debug.clicked.connect(self._clear_debug_log)
        btn_export_debug = QPushButton("Export log…")
        btn_export_debug.clicked.connect(self._export_debug_log)
        filters.addWidget(btn_clear_debug)
        filters.addWidget(btn_export_debug)
        debug_layout.addLayout(filters)

        self.debug_table = QTableWidget(0, 6)
        self._style_table(self.debug_table)
        self.debug_table.setHorizontalHeaderLabels(
            ["Timestamp", "Level", "Source", "Addr", "Event", "Detail"])
        # Event carries the SCPI error itself -- '-420,"Query UNTERMINATED"' --
        # which is the field a developer actually reads. Sizing it to content
        # keeps it whole; only the explanatory Detail column gets elided.
        for col in (0, 1, 2, 3, 4):
            self.debug_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self.debug_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        debug_layout.addWidget(self.debug_table)

        self.snoop_split.addWidget(debug_box)
        self.snoop_split.setStretchFactor(0, 3)
        self.snoop_split.setStretchFactor(1, 2)
        layout.addWidget(self.snoop_split, stretch=1)

    LEVEL_COLOURS = {"WARN": "warn", "ERROR": "bad"}

    def _clear_debug_log(self):
        self.debug_table.setRowCount(0)
        self.debug_records = []
        self.warning_count = 0
        self.error_count = 0
        self._refresh_debug_counts()

    def _refresh_debug_counts(self):
        self.warn_count_label.setText(
            "Warnings: %d   Errors: %d"
            % (getattr(self, "warning_count", 0), getattr(self, "error_count", 0)))

    def _apply_debug_filter(self):
        """Row visibility follows the level checkboxes."""
        wanted = {lvl for lvl, box in self.debug_filters.items() if box.isChecked()}
        for row in range(self.debug_table.rowCount()):
            item = self.debug_table.item(row, 1)
            self.debug_table.setRowHidden(
                row, item is not None and item.text() not in wanted)

    def _on_diagnostic_callback(self, record: Dict[str, Any]):
        """Called from a server thread; hand off to the GUI thread's queue."""
        try:
            self.warning_queue.put_nowait(record)
        except queue.Full:
            pass

    def _drain_warning_queue(self):
        drained = 0
        table = self.debug_table
        touched = False
        while drained < self.SNOOP_DRAIN_PER_TICK:
            try:
                record = self.warning_queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            touched = True

            level = record.get("level", "INFO")
            if level == "WARN":
                self.warning_count = getattr(self, "warning_count", 0) + 1
            elif level == "ERROR":
                self.error_count = getattr(self, "error_count", 0) + 1

            # Kept in insertion order so an export reads like a log file, even
            # though the table shows newest first.
            self.debug_records.append(record)

            address = record.get("address")
            table.insertRow(0)
            values = [
                record.get("timestamp", ""),
                level,
                record.get("source", ""),
                "" if address is None else str(address),
                record.get("event", ""),
                record.get("detail", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                token = self.LEVEL_COLOURS.get(level)
                if token and col in (1, 4):
                    item.setForeground(QColor(self.palette_tokens[token]))
                table.setItem(0, col, item)

        if touched:
            for _ in range(max(0, table.rowCount() - self.SNOOP_MAX_ROWS)):
                table.removeRow(table.rowCount() - 1)
            del self.debug_records[:-self.SNOOP_MAX_ROWS]
            self._refresh_debug_counts()
            self._apply_debug_filter()

    # --- export ------------------------------------------------------------
    def _export_debug_log(self):
        if not self.debug_records:
            QMessageBox.information(self, "Nothing to Export",
                                    "The debug log is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Debug Log",
            time.strftime("benchforge-debug-%Y%m%d-%H%M%S.log"),
            "Log files (*.log);;Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# BenchForge debug log  %s\n"
                             % time.strftime("%Y-%m-%d %H:%M:%S"))
                handle.write("# WARN lines are conditions real hardware would "
                             "have logged, not emulator faults.\n\n")
                for record in self.debug_records:
                    handle.write(format_record(record) + "\n")
            self.statusBar().showMessage("Debug log written to %s" % path, 8000)
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))

    def _export_traffic(self):
        table = self.snoop_table
        if table.rowCount() == 0:
            QMessageBox.information(self, "Nothing to Export",
                                    "The traffic feed is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Traffic",
            time.strftime("benchforge-traffic-%Y%m%d-%H%M%S.csv"),
            "CSV files (*.csv);;All files (*)")
        if not path:
            return

        def escape(value):
            return '"%s"' % value.replace('"', '""')

        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                headers = [table.horizontalHeaderItem(c).text()
                           for c in range(table.columnCount())]
                handle.write(",".join(escape(h) for h in headers) + "\n")
                # Bottom-up, so the file reads oldest-first like a capture.
                for row in range(table.rowCount() - 1, -1, -1):
                    cells = []
                    for col in range(table.columnCount()):
                        item = table.item(row, col)
                        cells.append(escape(item.text() if item else ""))
                    handle.write(",".join(cells) + "\n")
            self.statusBar().showMessage("Traffic written to %s" % path, 8000)
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))

    # --- ACTION HANDLERS ---
    def _refresh_device_table(self):
        self.device_table.setRowCount(0)
        for addr, dev in sorted(self.registry.devices.items()):
            row = self.device_table.rowCount()
            self.device_table.insertRow(row)

            slot_item = QTableWidgetItem(f"GPIB::{addr}")
            slot_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            name_item = QTableWidgetItem(dev.name)
            idn_item = QTableWidgetItem(dev.idn)

            self.device_table.setItem(row, 0, slot_item)
            self.device_table.setItem(row, 1, name_item)
            self.device_table.setItem(row, 2, idn_item)

    def _set_gateway_class(self, cls):
        """
        Replaces the gateway server when the emulated adapter changes.

        An AR488 is not a Prologix: it is case-insensitive, accepts ++default,
        allows wider argument ranges and reports its own version string. Serving
        Prologix behaviour under an AR488 label would mislead a client on every
        one of those points.
        """
        if type(self.server) is cls:
            return

        was_running = self.server._is_running
        host, port = self.server.host, self.server.port
        policy = self.server.connection_policy
        if was_running:
            self.server.stop()

        self.server = cls(host=host, port=port, registry=self.registry,
                          connection_policy=policy)
        self.server.add_packet_callback(self._on_packet_event_callback)
        self.server.add_diagnostic_callback(self._on_diagnostic_callback)
        self.server.synthetic_delay_ms = float(self.delay_slider.value()) \
            if hasattr(self, "delay_slider") else 0.0

        if was_running:
            try:
                self.server.start()
            except Exception:
                self._set_running_state(False)

    def _populate_bench(self, names=None):
        """
        Maps instruments from the shared default bench onto the registry.

        Every gateway mode presents at least four instruments so a client has a
        usable bench the moment the engine starts. Names come from DEFAULT_BENCH
        and match TestController's driver names exactly.
        """
        chosen = DEFAULT_BENCH if names is None else [
            spec for spec in DEFAULT_BENCH if spec["name"] in names
        ]

        # A name that no longer exists on the bench is silently dropped by the
        # filter above, which once left a mode presenting three instruments
        # while claiming four. Say so rather than quietly under-populating.
        if names is not None:
            missing = [n for n in names
                       if not any(s["name"] == n for s in DEFAULT_BENCH)]
            if missing:
                self.server.diagnose(
                    "WARN", "bench selection names unknown instruments",
                    "%s -- not present in DEFAULT_BENCH, so they were skipped"
                    % ", ".join(missing))
            if len(chosen) < 4:
                chosen = list(DEFAULT_BENCH)

        for spec in chosen:
            self.registry.set_device(int(spec["slot"]), build_instrument(spec))

    def _on_mode_changed(self, index):
        mode = self.mode_cb.currentText()

        custom = [a for a, d in self.registry.devices.items() if d.tc_definition is not None]
        if custom:
            slots_str = ", ".join(str(a) for a in sorted(custom))
            reply = QMessageBox.question(
                self,
                "Discard Assigned Drivers?",
                f"Switching emulation mode will clear custom TestController driver(s) assigned to GPIB slot(s) {slots_str}.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                idx = self.mode_cb.findText(self._previous_preset)
                if idx >= 0:
                    self.mode_cb.blockSignals(True)
                    self.mode_cb.setCurrentIndex(idx)
                    self.mode_cb.blockSignals(False)
                return

        self._previous_preset = mode
        self.registry.devices.clear()

        if "Keysight E5810A" in mode:
            self.port_lbl.hide()
            self.port_input.hide()
            self.lxi_port_lbl.show()
            self.lxi_port_input.show()
            if not self.lxi_port_input.text().isdigit():
                self.lxi_port_input.setText("5025")

            self._set_gateway_class(PrologixEmulatorServer)
            # The bench that is physically on this gateway.
            self._populate_bench()
            self.lxi_discovery.model_name = "Keysight E5810A Gateway"
            self.lxi_raw_server.default_address = slot_for("Agilent 34411A", 5)
        else:
            self.port_lbl.show()
            self.port_input.show()
            self.lxi_port_lbl.hide()
            self.lxi_port_input.hide()

            if "Prologix Ethernet" in mode:
                self._set_gateway_class(PrologixEmulatorServer)
                self._populate_bench()          # the full default bench
                self.lxi_discovery.model_name = "Prologix Ethernet Gateway"
                self.lxi_raw_server.default_address = slot_for("Keithley 2002", 1)
            elif "AR488" in mode:
                self._set_gateway_class(AR488EmulatorServer)
                self._populate_bench()
                self.lxi_discovery.model_name = "AR488 GPIB Gateway"
                self.lxi_raw_server.default_address = slot_for("Keithley 2002", 1)

        self._refresh_device_table()
        self.statusBar().showMessage(f"Applied emulation mode: {mode}", 5000)

    def _update_impairments(self, val):
        self.delay_lbl.setText(f"{val} ms")
        # The server converts ms to seconds itself; don't scale twice.
        self.server.synthetic_delay_ms = float(val)

    def _on_device_selected(self):
        items = self.parser_list.selectedItems()
        if not items:
            return
        inst_title = items[0].text()
        info_dict = self.PREBUILT_LIBRARY.get(inst_title)
        if not info_dict:
            return

        info = f"Instrument Model : {info_dict['name']}\n"
        info += f"Class            : {info_dict.get('class', 'DMM')}\n"
        info += f"Hardware *IDN?   : {info_dict['idn']}\n"
        # Entries carrying a 'bench' address were captured from that address on
        # the physical bus, so their replies are measured rather than composed.
        bench = info_dict.get("bench")
        info += ("Identity source  : measured on the physical bus at GPIB "
                 f"{bench}\n" if bench else
                 "Identity source  : reference values, not measured here\n")
        info += f"Description      : {info_dict['description']}\n"
        if info_dict.get("tc_driver") is False:
            info += ("\n!! NO TestController driver exists for this model. It "
                     "will emulate\n   correctly on the bus, but the client "
                     "cannot load it.\n")
        info += "=" * 55 + "\n\n"
        info += f"Supported SCPI Commands ({len(info_dict['commands'])} total):\n"
        for cmd in info_dict['commands']:
            info += f"  {cmd}\n"

        self.parser_text.setPlainText(info)

    def _assign_prebuilt_device_to_slot(self):
        items = self.parser_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "No Instrument Selected", "Please select a virtual instrument model from the library list first.")
            return
        inst_title = items[0].text()
        info_dict = self.PREBUILT_LIBRARY.get(inst_title)
        if not info_dict:
            return

        slot_num = self.assign_slot_spin.value()
        # A name that resolves to no driver leaves the generated
        # settingsLoad.txt entry unmatched and the client unable to load the
        # device, so say so before it is assigned rather than after.
        if info_dict.get("tc_driver") is False:
            proceed = QMessageBox.warning(
                self, "No TestController Driver",
                f"TestController ships no driver for the {info_dict['name']}.\n\n"
                "BenchForge will emulate it faithfully on the bus, but the "
                "client will not be able to load it.\n\nAssign it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if proceed != QMessageBox.StandardButton.Yes:
                return
        # Without the class a counter would be assigned as a DMM and would
        # then report VOLT, which client software rejects outright.
        v_dev = VirtualInstrument(
            gpib_address=slot_num,
            name=info_dict["name"],
            idn=info_dict["idn"],
            instrument_class=info_dict.get("class", "DMM"),
        )
        self.registry.set_device(slot_num, v_dev)
        self._refresh_device_table()
        self.statusBar().showMessage(
            f"Assigned '{info_dict['name']}' to GPIB Slot {slot_num}", 5000)
        QMessageBox.information(self, "Slot Assigned", f"Successfully assigned '{info_dict['name']}' to GPIB Slot {slot_num}.")

    def _generate_configs(self):
        device_mappings = []
        for addr, dev in sorted(self.registry.devices.items()):
            device_mappings.append({
                "name": dev.name,
                "gpib_address": addr,
                "enabled": 1,
            })

        port = int(self.port_input.text()) if self.port_input.text().isdigit() else 1234
        gpib_txt, load_txt = generate_recommended_configs(
            device_mappings, host=self.host_input.text(), port=port, mode="per_device_controller"
        )

        self.txt_gpib.setPlainText(gpib_txt)
        self.txt_load.setPlainText(load_txt)
        self.statusBar().showMessage(
            "Generated settingsGPIB.txt and settingsLoad.txt configurations.", 5000)

    def _export_file(self, default_name: str, content: str):
        path, _ = QFileDialog.getSaveFileName(self, "Save Config File", default_name, "Text Files (*.txt);;All Files (*.*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                QMessageBox.information(self, "Export Successful", f"Saved configuration file to:\n{path}")
            except OSError as exc:
                QMessageBox.warning(self, "Export Failed", f"Failed to export configuration file:\n{exc}")

    def _browse_tc_path(self):
        curr_path = self.tc_path_input.text().strip() if hasattr(self, "tc_path_input") else ""
        selected = QFileDialog.getExistingDirectory(self, "Select TestController Settings Folder", curr_path)
        if selected:
            self.tc_path_input.setText(selected)
            if not self._settings_ignored():
                self.settings.setValue("tc_path", selected)

    def _auto_configure_tc(self):
        path = self.tc_path_input.text().strip() if hasattr(self, "tc_path_input") else ""
        if not path:
            QMessageBox.warning(self, "Settings Folder Missing",
                                "Please specify or browse for your TestController Settings folder path first.")
            return

        if not os.path.isdir(path):
            QMessageBox.warning(self, "Settings Folder Not Found",
                                f"The specified TestController Settings folder path does not exist or is not a directory:\n{path}")
            return

        # Ensure text areas have active configuration strings
        self._generate_configs()

        gpib_txt = self.txt_gpib.toPlainText()
        load_txt = self.txt_load.toPlainText()

        gpib_file = os.path.join(path, "settingsGPIB.txt")
        load_file = os.path.join(path, "settingsLoad.txt")

        try:
            with open(gpib_file, "w", encoding="utf-8") as f:
                f.write(gpib_txt)
            with open(load_file, "w", encoding="utf-8") as f:
                f.write(load_txt)

            if not self._settings_ignored():
                self.settings.setValue("tc_path", path)

            self.statusBar().showMessage(f"Deployed settingsGPIB.txt and settingsLoad.txt to {path}", 5000)
            QMessageBox.information(
                self, "TestController Auto-Configured",
                f"Successfully deployed configuration files to TestController:\n\n"
                f"• {gpib_file}\n"
                f"• {load_file}\n\n"
                f"TestController is now ready to connect to BenchForge Studio."
            )
        except OSError as exc:
            QMessageBox.critical(self, "Deployment Failed",
                                 f"Failed to write configuration files to TestController directory:\n{exc}")

    # --- TRAFFIC SNOOP ---
    SNOOP_MAX_ROWS = 500
    SNOOP_DRAIN_PER_TICK = 50

    def _on_packet_event_callback(self, event_data: Dict[str, Any]):
        now = time.time()
        self.total_packet_count += 1
        lat = event_data.get("latency_ms", 0.0) or 0.0
        with self._telemetry_lock:
            self.qps_window.append(now)
            if lat > 0:
                self.latency_window.append(lat)
                if len(self.latency_window) > 100:
                    self.latency_window.pop(0)

        if self.snoop_paused:
            return
        try:
            self.snoop_queue.put_nowait(event_data)
        except queue.Full:
            pass  # Dropping packets under extreme load beats unbounded growth.

    def _drain_snoop_queue(self):
        drained = 0
        table = self.snoop_table
        table.setUpdatesEnabled(False)
        try:
            while drained < self.SNOOP_DRAIN_PER_TICK:
                try:
                    event_data = self.snoop_queue.get_nowait()
                except queue.Empty:
                    break
                self._insert_snoop_row(event_data)
                drained += 1

            excess = table.rowCount() - self.SNOOP_MAX_ROWS
            for _ in range(max(0, excess)):
                table.removeRow(table.rowCount() - 1)
        finally:
            table.setUpdatesEnabled(True)

        self._refresh_telemetry()

    def _refresh_telemetry(self):
        """Updates the tiles and pushes one sample onto the throughput trace."""
        now = time.time()
        with self._telemetry_lock:
            self.qps_window = [t for t in self.qps_window if now - t <= 1.0]
            qps = len(self.qps_window)
            latencies = list(self.latency_window)
        avg_lat = (sum(latencies) / len(latencies)) if latencies else 0.0
        active_count = len(self.server.active_clients) + len(self.lxi_raw_server.active_clients)

        # Thousands separators keep large packet counts readable; the tile font
        # is tabular so the digits do not shuffle as they change.
        self.tile_throughput.set_value(f"{qps:,}", good=qps > 0)
        self.tile_latency.set_value(f"{avg_lat:.2f}" if avg_lat else "—")
        self.tile_clients.set_value(f"{active_count}", good=active_count > 0)
        self.tile_packets.set_value(f"{self.total_packet_count:,}")

        # Sample at the drain interval (50 ms) but only trace every 4th tick,
        # so two minutes of history fits the sparkline's capacity.
        self._spark_tick = getattr(self, "_spark_tick", 0) + 1
        if self._spark_tick % 4 == 0:
            self.telem_spark.push(qps)
        self._set_label(self.sb_clients, f"Clients {active_count}")

    def _insert_snoop_row(self, event_data: Dict[str, Any]):
        """Inserts one packet at the top of the grid (newest first)."""
        table = self.snoop_table
        table.insertRow(0)

        direction = str(event_data.get("direction", ""))
        text = str(event_data.get("text", ""))

        # Controller commands (++ver, ++addr) are not addressed to an
        # instrument, so the slot column stays blank rather than claiming one.
        is_controller_cmd = text.startswith("++")
        slot = "" if is_controller_cmd else f"GPIB {event_data.get('address', '')}"

        latency = event_data.get("latency_ms", 0.0) or 0.0
        latency_text = f"{latency:.2f}" if latency > 0 else "—"

        values = [
            str(event_data.get("timestamp", "")),
            str(event_data.get("client", "")),
            direction,
            slot,
            text,
            latency_text,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col in (0, 4):
                item.setFont(QFont(theme.resolve_data_font()))
            if col == 5:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setFont(QFont(theme.resolve_data_font()))
            if col in (2, 3):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, col, item)

    def _toggle_snoop_pause(self, checked: bool = None):
        self.snoop_paused = (not self.snoop_paused) if checked is None else checked
        self.act_pause_snoop.setChecked(self.snoop_paused)
        self.statusBar().showMessage(
            "Traffic feed paused." if self.snoop_paused else "Traffic feed running.", 3000
        )

    def _clear_snoop(self):
        self.snoop_table.setRowCount(0)
        self.total_packet_count = 0
        with self._telemetry_lock:
            self.latency_window.clear()
            self.qps_window.clear()
        if hasattr(self, "telem_spark"):
            self.telem_spark.clear()
        while True:
            try:
                self.snoop_queue.get_nowait()
            except queue.Empty:
                break

    # --- SERVER TOGGLE ---
    def _set_running_state(self, running: bool):
        """Single place that reflects listener state into the chrome."""
        self.act_start.setEnabled(not running)
        self.act_stop.setEnabled(running)
        self.act_restart.setEnabled(running)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_restart.setEnabled(running)
        self._refresh_status_led()

    def _start_servers(self, silent: bool = False):
        """
        Binds the listeners for the active gateway mode.

        `silent` suppresses the modal on failure, for the automatic start at
        launch: a dialog in front of a window the user has not seen yet is
        worse than a clear status line they can act on.
        """
        mode = self.mode_cb.currentText()
        is_e5810a = "Keysight E5810A" in mode

        running = self.lxi_raw_server._is_running if is_e5810a else self.server._is_running
        if running:
            return

        err1 = None
        if is_e5810a:
            l_port = int(self.lxi_port_input.text()) if self.lxi_port_input.text().isdigit() else 5025
            self.lxi_raw_server.host = self.host_input.text()
            self.lxi_raw_server.port = l_port
            try:
                self.lxi_raw_server.start()
                self.lxi_discovery.start()
                # The E5810A's real interface is VXI-11 over ONC-RPC, not a raw
                # SCPI socket. Binding 111 can need privileges, so a failure
                # here degrades to the raw socket rather than killing the mode.
                self.vxi11_server.host = self.host_input.text()
                try:
                    self.vxi11_server.start()
                except Exception as exc:
                    self.vxi11_server.diagnose(
                        "ERROR", "VXI-11 listener could not bind",
                        "%s -- ports %d/%d. Port 111 may need elevated "
                        "privileges or be held by another RPC service."
                        % (exc, self.vxi11_server.portmap_port,
                           self.vxi11_server.core_port))
            except Exception as e:
                err1 = e
        else:
            p_port = int(self.port_input.text()) if self.port_input.text().isdigit() else 1234
            self.server.host = self.host_input.text()
            self.server.port = p_port
            try:
                self.server.start()
            except Exception as e:
                err1 = e

        if err1:
            self.server.stop()
            self.lxi_raw_server.stop()
            self.lxi_discovery.stop()
            self._set_running_state(False)
            port = (self.lxi_port_input.text() if is_e5810a else self.port_input.text())
            self._set_label(self.sb_state, "Stopped")
            if silent:
                self.statusBar().showMessage(
                    f"Engine did not start — port {port} is unavailable ({err1}). "
                    f"Change the port on Adapter & Presets, then press Start.",
                    15000
                )
            else:
                QMessageBox.critical(
                    self, "Engine did not start",
                    f"Could not bind port {port}.\n\nError: {err1}\n\n"
                    f"Another application may already be using it. Change the "
                    f"port on Adapter & Presets and try again."
                )
            return

        self._set_running_state(True)
        self._set_label(self.sb_state, "Running")
        if is_e5810a:
            l_port = int(self.lxi_port_input.text()) if self.lxi_port_input.text().isdigit() else 5025
            self._set_label(self.sb_bind, f"{self.host_input.text()}:{l_port}")
            self.statusBar().showMessage(f"Engine running: LXI SCPI Port {l_port}, mDNS 5353.", 4000)
        else:
            p_port = int(self.port_input.text()) if self.port_input.text().isdigit() else 1234
            self._set_label(self.sb_bind, f"{self.host_input.text()}:{p_port}")
            self.statusBar().showMessage(f"Engine running: Gateway Port {p_port}.", 4000)

    def _stop_servers(self):
        if not (self.server._is_running or self.lxi_raw_server._is_running):
            return
        self.server.stop()
        self.lxi_raw_server.stop()
        self.lxi_discovery.stop()
        self.vxi11_server.stop()
        self._set_running_state(False)
        self._set_label(self.sb_state, "Ready")
        self._set_label(self.sb_clients, "Clients 0")
        self.statusBar().showMessage("Emulators stopped.", 4000)

    def _restart_servers(self):
        self._stop_servers()
        self._start_servers()

    def _on_autostart_toggled(self, checked: bool):
        self.settings.setValue("auto_start_engine", checked)
        msg = "Auto-start engine on launch ENABLED." if checked else "Auto-start engine on launch DISABLED."
        self.statusBar().showMessage(msg, 3000)

    def closeEvent(self, event):
        try:
            if self._settings_ignored():
                # Do not write back either -- a verification run must not
                # leave the developer's saved session altered.
                self.server.stop()
                self.lxi_raw_server.stop()
                self.lxi_discovery.stop()
                self.vxi11_server.stop()
                event.accept()
                return
            self.settings.setValue("auto_start_engine", self.act_auto_start.isChecked())
            self.settings.setValue("last_emulation_mode", self.mode_cb.currentText())
            self.settings.setValue("last_host", self.host_input.text())
            if self.port_input.isVisible():
                self.settings.setValue("last_port", self.port_input.text())
            elif self.lxi_port_input.isVisible():
                self.settings.setValue("last_port", self.lxi_port_input.text())
            self.settings.setValue("last_query_delay", self.delay_slider.value())
            if hasattr(self, "tc_path_input"):
                self.settings.setValue("tc_path", self.tc_path_input.text().strip())

            self.server.stop()
            self.lxi_raw_server.stop()
            self.lxi_discovery.stop()
            self.vxi11_server.stop()
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = BenchForgeQtApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
