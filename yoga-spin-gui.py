#!/usr/bin/python
# -*- coding: utf-8 -*-


import sys
import os
import signal
import socket
import logging

from PyQt5.QtWidgets import QApplication, QWidget, QDesktopWidget, QPushButton, QSystemTrayIcon, QMenu, QHBoxLayout, QVBoxLayout
from PyQt5.QtGui import QIcon, QKeyEvent
from PyQt5.QtCore import QSize, QObject, pyqtSignal, QTimer, Qt


class ScreenControlState(object):
    MODE_LAPTOP = 0
    MODE_TABLET = 1

    def __init__(self):
        # init defaults
        self.mode = self.MODE_LAPTOP
        self.orientation = 0 # up
        self.lockRotation = True
        self.enableTouch = True
    # enddef
#endclass


class SpinServerProxy(object):
    """
    Interface to the yoga-spin server
    """
    _SPIN_SOCKET = '/tmp/yoga_spin.socket'
    
    def __init__(self):
        pass

    def SetState(self, state):
        # mode
        print "*** Setting Mode ***"
        if state.mode == ScreenControlState.MODE_TABLET:
            self._send_command("tablet")
        else:
            self._send_command("laptop")

        # orientation
        log.debug("*** Setting Orientation ***")
        orientationCommands = ("normal", "right", "inverted", "left")
        self._send_command(orientationCommands[state.orientation])

        # touch screen
        log.debug("*** Setting Touch Screen ***")
        self._send_command("touchenable" if state.enableTouch else "touchdisable")
        
        # accelerometer
        log.debug("*** Setting Rotation Lock ***")
        self._send_command("rotatelock" if state.lockRotation else "rotateunlock")
        log.debug("*** Done ***")
    #enddef

    def _send_command(self, command):
        if os.path.exists(self._SPIN_SOCKET):
            command_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                command_socket.connect(self._SPIN_SOCKET)
                command_socket.send(command)
                log.debug("Connected to socket")
            except Exception, e:
                log.error("Failed to send mode change to the spin daemon: %s" % (e, ))
        else:
            log.error("Socket does not exist. Is the spin deamon running.")
    #enddef

#endclass


class TouchKeyboardHandler(object):
    _runCommand = "/usr/bin/onboard"  # TODO - is a python application and could be run directly

    def __init__(self):
        self._pid = None

    def Start(self):
        if not self._pid:
            self._pid = os.spawnl(os.P_NOWAIT, self._runCommand, "onboard")
            log.debug("Started command %d" % (self._pid, ))
    #enddef

    def Close(self):
        if self._pid != None:
            os.kill(self._pid, signal.SIGTERM)
            self._pid = None
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

        display_position_event = "ibm/hotkey LEN0068:00 00000080 000060c0\n"
        rotation_lock_event = "ibm/hotkey LEN0068:00 00000080 00006020\n"

        if event_ACPI == rotation_lock_event:
            pass
        elif event_ACPI == display_position_event:
            log.info("Display position changed. Event not implemented.")
            self.spinSignal.emit("display_position_change")
        else:
            log.debug("Unknown acpi event triggered: {0}".format(event_ACPI))

    #enddef
# endclass


class Controller(object):
    def __init__(self):
        self._view = None
        self._state = ScreenControlState()
        self._serverProxy = SpinServerProxy()
        self._touchKeyboard = TouchKeyboardHandler()
 
    def SetView(self, view):
        self._view = view

    def GetState(self):
        return self._state

    def OnActivateToggle(self):
        self._view.Show(not self._view.IsVisible())

    def OnActivate(self):
        if not self._view.IsVisible():
            self._view.Show()

    def OnToggleTouch(self, status):
        self._state.enableTouch = status
        log.debug("Touch enabled: %d" % ((1 if status else 0), ))

    def OnToggleOrientationLock(self, status):
        self._state.lockRotation = status
        log.debug("Orientation locked: %d" % ((1 if status else 0), ))

    def OnChangeOrientation(self, orientation):
        self._state.orientation = orientation
        log.debug("Orientation set: %d" % (orientation, ))

    def OnSubmitMode(self, mode):
        self._state.mode = mode
        self._view.Show(False)

        self._serverProxy.SetState(self._state)

        log.info("Mode submitted: %d" % (mode, ))
    # enddef

    def HandleIncomingEvent(self, command):
        if command == "display_position_change":
            self.OnActivate()
    #enddef

    def OnWindowClosed(self):
        if self._state.mode == ScreenControlState.MODE_LAPTOP:
            self._touchKeyboard.Close()
        else:
            self._touchKeyboard.Start()
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
    SUBMIT_BTN_SIZE = 100
    SWITCH_BTN_SIZE = 80

    SCREEN_ORIENTATION_ICON = ('orientation-up.svg', 'orientation-right.svg', 'orientation-down.svg', 'orientation-left.svg')

    def _toggle_touch_icon(self, enabled):
        return self._iconPath + "/" + ("toggle-touch.svg" if enabled else "toggle-touch-off.svg")

    def _toggle_rotation_icon(self, locked):
        return self._iconPath + "/" + ("toggle-lock.svg" if locked else "toggle-unlock.svg")

    def __init__(self, app, controller, iconPath = "./"):
        self._controller = controller
        self._iconPath = iconPath
        initState = controller.GetState()

        # 0=up, 1=right, 2=down, 3=left
        self._screenOrientation = initState.orientation

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

        btnScreenOri = QPushButton()
        btnScreenOri.setIcon(QIcon(iconPath + "/" + self.SCREEN_ORIENTATION_ICON[self._screenOrientation]))
        btnScreenOri.setIconSize(QSize(self.SWITCH_BTN_SIZE, self.SWITCH_BTN_SIZE))
        btnScreenOri.setToolTip('Select screen orientation.')
        btnScreenOri.resize(btnScreenOri.sizeHint())
        btnScreenOri.clicked.connect(self.EventChangeOrientation)
        self._screenOrientationBtn = btnScreenOri

        customHBox = QHBoxLayout()
        customHBox.addStretch()
        customHBox.addWidget(btnTouch)
        customHBox.addWidget(btnScreenLck)
        customHBox.addWidget(btnScreenOri)
        customHBox.addStretch()


        btnLaptop = QPushButton()
        btnLaptop.setIcon(QIcon(iconPath + "/mode-laptop.svg"))
        btnLaptop.setIconSize(QSize(self.SUBMIT_BTN_SIZE, self.SUBMIT_BTN_SIZE))
        btnLaptop.setToolTip('Pick <b>laptop mode</b> preset.')
        btnLaptop.resize(btnLaptop.sizeHint())
        btnLaptop.clicked.connect(self.EventSubmitLaptopMode)

        btnTablet = QPushButton()
        btnTablet.setIcon(QIcon(iconPath + "/mode-tablet.svg"))
        btnTablet.setIconSize(QSize(self.SUBMIT_BTN_SIZE, self.SUBMIT_BTN_SIZE))
        btnTablet.setToolTip('Pick <b>tablet mode</b> preset.')
        btnTablet.resize(btnTablet.sizeHint())
        btnTablet.clicked.connect(self.EventSubmitTabletMode)

        modeHBox = QHBoxLayout()
        modeHBox.addStretch()
        modeHBox.addWidget(btnLaptop)
        modeHBox.addWidget(btnTablet)
        modeHBox.addStretch()

        vbox = QVBoxLayout()
        vbox.addLayout(customHBox)
        vbox.addLayout(modeHBox)
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
        self._window.setWindowIcon(QIcon(iconPath + '/icon.svg'))

        self._window.keyPressed.connect(self.EventKeyPressed)
        self._window.closed.connect(self._controller.OnWindowClosed)
    # enddef

    def EventChangeOrientation(self):
        self._screenOrientation = (self._screenOrientation + 1) % 4
        self._screenOrientationBtn.setIcon(QIcon(self._iconPath + "/" + self.SCREEN_ORIENTATION_ICON[self._screenOrientation]))
        self._controller.OnChangeOrientation(self._screenOrientation)
    # enddef

    def EventToggleTouch(self, checked):
        self._toggleTouchBtn.setIcon(QIcon(self._toggle_touch_icon(checked)))
        self._controller.OnToggleTouch(checked)
    #enddef

    def EventToggleLock(self, checked):
        self._rotationLockBtn.setIcon(QIcon(self._toggle_rotation_icon(checked)))
        self._controller.OnToggleOrientationLock(checked)
    #enddef

    def EventSubmitLaptopMode(self):
        self._controller.OnSubmitMode(ScreenControlState.MODE_LAPTOP)
    #enddef

    def EventSubmitTabletMode(self):
        self._controller.OnSubmitMode(ScreenControlState.MODE_TABLET)
    #enddef

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
    def __init__(self, app, controller, iconPath = "./"):
        """
        """
        self._controller = controller
        self._iconPath = iconPath

        # system tray
        self._trayIcon = QSystemTrayIcon(QIcon(iconPath + "/icon-sq.png"), app)
        menu = QMenu("Lid Control")

        activateAction = menu.addAction("Lid Control")
        activateAction.triggered.connect(self.MenuShowActivated)

        menu.addSeparator()

        # exit menu entry
        exitAction = menu.addAction(QIcon(iconPath + "/icon-exit.svg"), "Exit")
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

def ResolveResourcesPath():
    if os.getcwd().find("/usr/bin") == 0:
        return "/usr/share/yoga-spin-gui/art"
    else:
        return "./art"
#enddef

if __name__ == '__main__':
    global log
    log = logging.getLogger()
    logHandler = logging.StreamHandler()
    log.addHandler(logHandler)
    logHandler.setFormatter(logging.Formatter("[%(asctime)-15s] (" + str(os.getpid()) + ") %(message)s"))
    log.level = logging.INFO

    iconPath = ResolveResourcesPath()

    app = QApplication(sys.argv)

    controller = Controller()

    tray = LidControlMenu(app, controller, iconPath)
    tray.Show()

    view = LidControlView(app, controller, iconPath)
    view.Show()

    eventListener = EventListener()
    eventListener.spinSignal.connect(controller.HandleIncomingEvent)

    controller.SetView(view)

    sys.exit(app.exec_())

# notes
# * automatically enable touch screen when lid position changes so that you can control the dialogue
# * an easy way to enable touch
# * large close button 



