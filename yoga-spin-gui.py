#!/usr/bin/python
# -*- coding: utf-8 -*-


import sys
import os
import signal
import socket
import logging
import getopt
import subprocess
import ConfigParser

from PyQt5.QtWidgets import QApplication, QWidget, QDesktopWidget, QPushButton, QSystemTrayIcon, QMenu, QHBoxLayout, QVBoxLayout
from PyQt5.QtGui import QIcon, QKeyEvent
from PyQt5.QtCore import QSize, QObject, pyqtSignal, QTimer, Qt


class Config(object):
    def __init__(self, fileName = None):
         #default values
        self.logFile = './yoga-spin-gui.log'
        self.logLevel = "debug"
        self.iconPath = './art/'
        self.pidFile = './yoga-spin-gui.pid'
        self.touchKeyboardCmd = '/usr/bin/onboard'

        if not fileName:
            return

        parser = ConfigParser.ConfigParser()
        parser.read(fileName)

        self.logFile = self._get_option(parser, "control", "logFile", self.logFile)
        self.logLevel = self._get_option(parser, "control", "logLevel", self.logLevel)
        self.iconPath = self._get_option(parser, "gui", "iconPath", self.iconPath)
        self.pidFile = self._get_option(parser, "control", "pidFile", self.pidFile)
        self.touchKeyboardCmd = self._get_option(parser, "touch-keyboard", "command", self.touchKeyboardCmd)
    #enddef

    def _get_option(self, parser, section, option, default):
        if parser.has_option(section, option):
            return parser.get(section, option)

        log.debug("Missing option [%s]:%s", (section, option))
        return default

    def InitLogging(self):
        if self.logLevel == "debug":
            log.level = logging.DEBUG
        elif self.logLevel == "info":
            log.level = logging.INFO
        elif self.logLevel == "warning":
            log.level = logging.WARNING
        elif self.logLevel == "error":
            log.level = logging.ERROR

        if self.logFile:
            handler = logging.FileHandler(self.logFile)
            handler.setFormatter(logging.Formatter("[%(asctime)-15s] (" + str(os.getpid()) + ") %(message)s"))
            log.addHandler(handler)
#endclass


class ScreenControlState(object):
    MODE_LAPTOP = 0
    MODE_TABLET = 1

    def __init__(self):
        # init defaults
        self.mode = self.MODE_LAPTOP
        self.lockRotation = True
        self.enableTouch = True
    # enddef
#endclass


class TouchKeyboardHandler(object):
    def __init__(self):
        self._pid = None

    def Start(self):
        if not self._pid:
            self._pid = os.spawnl(os.P_NOWAIT, config.touchKeyboardCmd, "onboard")
            log.debug("Started command %d" % (self._pid, ))
    #enddef

    def Close(self):
        if self._pid != None:
            os.kill(self._pid, signal.SIGTERM)
            self._pid = None
    #enddef
#endclass


class XInputProxy(object):
    def __init__(self):
        self._deviceNames = {}

    def InitDeviceList(self):
        log.info("Audit Inputs:")
        input_devices = subprocess.Popen(
            ["xinput", "list", "--name-only"],
            stdin = subprocess.PIPE,
            stdout = subprocess.PIPE,
            stderr = subprocess.PIPE
        ).communicate()[0]

        devices_and_keyphrases = {
            "touchscreen": ["SYNAPTICS Synaptics Touch Digitizer V04",
                            "ELAN Touchscreen",
                            "Wacom Co.,Ltd. Pen and multitouch sensor Finger touch",
                            "Wacom Pen and multitouch sensor Finger touch"],
            "touchpad":    ["PS/2 Synaptics TouchPad",
                            "SynPS/2 Synaptics TouchPad",
                            "AlpsPS/2 ALPS DualPoint TouchPad"],
        }

        for device, keyphrases in devices_and_keyphrases.iteritems():
            for keyphrase in keyphrases:
                if keyphrase in input_devices:
                    self._deviceNames[device] = keyphrase

        for device, keyphrases in devices_and_keyphrases.iteritems():
            if device in self._deviceNames:
                log.info(" - {device} detected as \"{deviceName}\"".format(
                    device     = device.title(),
                    deviceName = self._deviceNames[device]
                ))
            else:
                log.info(" - {device} not detected".format(
                    device = device.title()
                ))
    #enddef

    def TouchscreenSwitch(self, status = None):
        if "touchscreen" in self._deviceNames:
            xinput_status = {
                True:  "enable",
                False: "disable"
            }

            if xinput_status.has_key(status):
                log.info("{status} touchscreen".format(
                    status = xinput_status[status].title()
                ))
                os.system(
                    "xinput {status} \"{device_name}\"".format(
                        status = xinput_status[status],
                        device_name = self._deviceNames["touchscreen"]
                    )
                )
            else:
                log.error("Unknown touchscreen status \"{0}\" requested".format(status))
                sys.exit()
        else:
            log.debug("Touchscreen status unchanged")
    #enddef
    
    def TouchpadSwitch(self, status = None):
        if "touchpad" in self._deviceNames:
            xinput_status = {
                True:  "enable",
                False: "disable"
            }

            if xinput_status.has_key(status):
                log.info("{status} touchpad".format(
                    status = xinput_status[status].title()
                ))
                os.system(
                    "xinput {status} \"{device_name}\"".format(
                        status = xinput_status[status],
                        device_name = self._deviceNames["touchpad"]
                    )
                )
            else:
                log.error("Unknown touchpad status \"{0}\" requested".format(status))
                sys.exit()
        else:
            log.debug("Touchpad status unchanged")
    #enddef
#endclass


class EventListener(QObject):
    # signal for announcing received events
    # takes two arguments <event type> and <event data>
    spinSignal = pyqtSignal('QString')

    def __init__(self):
        # init parent
        super(EventListener, self).__init__()

        # receive events through the ACPI socket
        self._socket_ACPI = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket_ACPI.connect("/var/run/acpid.socket")
        self._socket_ACPI.setblocking(0)

        # start the 50ms timer
        self._timer = QTimer()
        self._timer.timeout.connect(self.Time)
        self._timer.start(50)
    # enddef

    def Time(self):
        event_ACPI = None
        try:
            event_ACPI = self._socket_ACPI.recv(4096)
        except Exception, e:
            #log.error("Failed to fetch command from socket. Reason: %s" % (e, ))
            return
        if not event_ACPI :
            return

        log.debug("ACPI event: {0}".format(event_ACPI))

        rotation_lock_event = "ibm/hotkey LEN0068:00 00000080 00006020\n"
        tablet_mode_event = "video/tabletmode TBLT 0000008A 00000001\n"
        laptop_mode_event = "video/tabletmode TBLT 0000008A 00000000\n"

        if event_ACPI == rotation_lock_event:
            pass
        elif event_ACPI == tablet_mode_event:
            log.info("Display position changed to tablet mode.")
            self.spinSignal.emit("display_position_tablet")
        elif event_ACPI == laptop_mode_event:
            log.info("Display position changed to laptop mode.")
            self.spinSignal.emit("display_position_laptop")
        else:
            log.debug("Unknown acpi event triggered: {0}".format(event_ACPI))

    #enddef
# endclass


class Controller(object):
    def __init__(self):
        self._view = None
        self._state = ScreenControlState()
        self._touchKeyboard = TouchKeyboardHandler()
        self._xInputProxy = XInputProxy()

        self._xInputProxy.InitDeviceList()
 
    def SetView(self, view):
        self._view = view

    def GetState(self):
        return self._state

    def OnActivateToggle(self):
        self._view.Show(not self._view.IsVisible())

    def OnActivate(self):
        if not self._view.IsVisible():
            self._view.Show()
            if not self._state.enableTouch:
                self.OnToggleTouch(True)
        #endif
    #enddef

    def OnToggleTouch(self, status):
        self._state.enableTouch = status
        self._xInputProxy.TouchscreenSwitch(status)
        self._view.SetTouchEnableState(status)
        log.debug("Touch enabled: %d" % ((1 if status else 0), ))

    def OnToggleOrientationLock(self, status):
        self._state.lockRotation = status
        log.debug("Orientation locked: %d" % ((1 if status else 0), ))

    def OnTabletModeEnter(self):
        self._touchKeyboard.Start()
        self._xInputProxy.TouchpadSwitch(False)
        self.OnToggleTouch(True)
    #enddef

    def OnLaptopModeEnter(self):
        self._touchKeyboard.Close()
        self._xInputProxy.TouchpadSwitch(True)
    #enddef

    def HandleIncomingEvent(self, command):
        if command == "display_position_tablet":
            self._state.mode = ScreenControlState.MODE_TABLET
            self.OnTabletModeEnter()
        elif command == "display_position_laptop":
            self._state.mode = ScreenControlState.MODE_LAPTOP
            self.OnLaptopModeEnter()
    #enddef

    def OnWindowClosed(self):
        pass
    #enddef

# endclass


class KeyHandlingWidget(QWidget):
    keyPressed = pyqtSignal(QKeyEvent)
    closed = pyqtSignal()

    def keyPressEvent(self, event):
        super(KeyHandlingWidget, self).keyPressEvent(event)
        self.keyPressed.emit(event)

    def closeEvent(self, event):
        """
        Announces closing the window by clicking the cross button in the top-right corner. Not triggered by setVisible(False).
        """
        self.closed.emit()
        super(KeyHandlingWidget, self).closeEvent(event)
#endclass


class LidControlView(object):
    SWITCH_BTN_SIZE = 80

    def _toggle_touch_icon(self, enabled):
        return config.iconPath + ("toggle-touch.svg" if enabled else "toggle-touch-off.svg")

    def _toggle_rotation_icon(self, locked):
        return config.iconPath + ("toggle-lock.svg" if locked else "toggle-unlock.svg")

    def __init__(self, app, controller):
        self._controller = controller
        initState = controller.GetState()

        btnTouch = QPushButton()
        btnTouch.setCheckable(True)
        btnTouch.setIcon(QIcon(self._toggle_touch_icon(initState.enableTouch)))
        btnTouch.setIconSize(QSize(self.SWITCH_BTN_SIZE, self.SWITCH_BTN_SIZE))
        btnTouch.setToolTip('Enable touch screen.')
        btnTouch.resize(btnTouch.sizeHint())
        btnTouch.setChecked(initState.enableTouch)
        btnTouch.toggled.connect(self.EventToggleTouch)
        self._toggleTouchBtn = btnTouch

        btnScreenLck = QPushButton()
        btnScreenLck.setCheckable(True)
        btnScreenLck.setIcon(QIcon(self._toggle_rotation_icon(initState.lockRotation)))
        btnScreenLck.setIconSize(QSize(self.SWITCH_BTN_SIZE, self.SWITCH_BTN_SIZE))
        btnScreenLck.setToolTip('Lock screen not to be rotated by accelerometer.')
        btnScreenLck.resize(btnScreenLck.sizeHint())
        btnScreenLck.setChecked(initState.lockRotation)
        btnScreenLck.toggled.connect(self.EventToggleLock)
        self._rotationLockBtn = btnScreenLck

        customHBox = QHBoxLayout()
        customHBox.addStretch()
        customHBox.addWidget(btnTouch)
        customHBox.addWidget(btnScreenLck)
        customHBox.addStretch()

        vbox = QVBoxLayout()
        vbox.addLayout(customHBox)
        vbox.addStretch()

        # main window
        self._window = KeyHandlingWidget()
        self._window.setLayout(vbox)
        self._window.setWindowModality(2)
        self._window.setWindowFlags(Qt.WindowStaysOnTopHint)

        # position the window to the center of the screen
        frameRect = self._window.frameGeometry()
        centerPos = QDesktopWidget().availableGeometry().center()
        frameRect.moveCenter(centerPos)
        self._window.move(frameRect.topLeft())

        # set window title and icon
        self._window.setWindowTitle('Lid Control')
        self._window.setWindowIcon(QIcon(config.iconPath + 'icon.svg'))

        self._window.keyPressed.connect(self.EventKeyPressed)
        self._window.closed.connect(self._controller.OnWindowClosed)
    # enddef

    def EventToggleTouch(self, checked):
        self._toggleTouchBtn.setIcon(QIcon(self._toggle_touch_icon(checked)))
        self._controller.OnToggleTouch(checked)
    #enddef

    def EventToggleLock(self, checked):
        self._rotationLockBtn.setIcon(QIcon(self._toggle_rotation_icon(checked)))
        self._controller.OnToggleOrientationLock(checked)
    #enddef

    def SetTouchEnableState(self, enable):
        self._toggleTouchBtn.setIcon(QIcon(self._toggle_touch_icon(enable)))
        self._toggleTouchBtn.setChecked(enable)

    def Show(self, show = True):
        if self.IsVisible() and not show :
            self._controller.OnWindowClosed()

        self._window.setVisible(show)
    #enddef

    def IsVisible(self):
        return self._window.isVisible()

    def EventKeyPressed(self, event):
        if event.key() == Qt.Key_Escape:
            self.Show(False)
#endclass


class LidControlMenu(object):
    def __init__(self, app, controller):
        """
        """
        self._controller = controller

        # system tray
        self._trayIcon = QSystemTrayIcon(QIcon(config.iconPath + "icon-sq.png"), app)
        menu = QMenu("Lid Control")

        activateAction = menu.addAction("Lid Control")
        activateAction.triggered.connect(self.MenuShowActivated)

        menu.addSeparator()

        # exit menu entry
        exitAction = menu.addAction(QIcon(config.iconPath + "icon-exit.svg"), "Exit")
        exitAction.triggered.connect(app.quit)
        self._trayIcon.setContextMenu(menu)

        # handle activation
        self._trayIcon.activated.connect(self.IconActivated)
    # endif

    def IconActivated(self):
        controller.OnActivateToggle()

    def MenuShowActivated(self):
        controller.OnActivate()

    def Show(self):
        self._trayIcon.show()

    def ShowMessage(self, title, message):
        self._trayIcon.showMessage(title, message)
#endclass


def Usage():
    print "%s [-h] [-f <config file>]" % (sys.argv[0], )


if __name__ == '__main__':
    global log
    log = logging.getLogger()
    logHandler = logging.StreamHandler()
    log.addHandler(logHandler)
    logHandler.setFormatter(logging.Formatter("[%(asctime)-15s] (" + str(os.getpid()) + ") %(message)s"))
    log.level = logging.DEBUG

    try:
        opts, args = getopt.getopt(sys.argv[1:],"hf:")
    except getopt.GetoptError:
        Usage()
        sys.exit(2)

    configFile = None

    for opt, arg in opts:
        if opt == '-h':
            Usage()
            sys.exit()
        elif opt == "-f":
            configFile = arg
    #endfor

    global config
    log.debug("Reading config from %s" % (configFile, ))
    config = Config(configFile)
    config.InitLogging()

    log.info("Started, loading icons from " + config.iconPath)

    app = QApplication(sys.argv)
    QApplication.setApplicationDisplayName('Yoga Spin GUI')

    controller = Controller()

    tray = LidControlMenu(app, controller)
    tray.Show()

    view = LidControlView(app, controller)

    eventListener = EventListener()
    eventListener.spinSignal.connect(controller.HandleIncomingEvent)

    controller.SetView(view)

    sys.exit(app.exec_())

# notes
# * configure logging properly
# * pidfile
# * don't run (and then close) onboard if it was already running
# * large close button 



