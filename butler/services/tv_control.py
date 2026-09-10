import subprocess
import time
from typing import List, Optional


ADB_PATH = "adb"
TV_IP = "192.168.1.158"  
TV_PORT = 5555         

KEY_POWER_TOGGLE = "26"
KEY_SLEEP        = "223"
KEY_WAKE         = "224"  

KEY_HDMI1 = "243"
KEY_HDMI2 = "244"
KEY_HDMI3 = "245"


class FireTvController:
    def __init__(self, adb_path: str = ADB_PATH,
                 ip: str = TV_IP,
                 port: int = TV_PORT) -> None:
        self.adb_path = adb_path
        self.target = f"{ip}:{port}"

    def _run_adb(self, args: List[str],
                 check: bool = False,
                 capture_output: bool = True,
                 text: bool = True) -> subprocess.CompletedProcess:
        
        cmd = [self.adb_path, "-s", self.target] + args
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=text
        )

    def _run_adb_raw(self, args: List[str],
                     check: bool = False,
                     capture_output: bool = True,
                     text: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path] + args
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=text
        )

    def connect(self) -> bool:
        result = self._run_adb_raw(["connect", self.target])
        print(result.stdout.strip() or result.stderr.strip())
        return "connected to" in result.stdout.lower() or "already connected" in result.stdout.lower()

    def disconnect(self) -> None:
        result = self._run_adb_raw(["disconnect", self.target])
        print(result.stdout.strip() or result.stderr.strip())

    def is_connected(self) -> bool:
        result = self._run_adb_raw(["devices"])
        devices = result.stdout.strip().splitlines()[1:]  # skip header
        for line in devices:
            if self.target in line and "device" in line:
                return True
        return False

    def send_key(self, keycode: str) -> None:
        self._run_adb(["shell", "input", "keyevent", keycode])

    def send_key_name(self, name: str) -> None:
        self._run_adb(["shell", "input", "keyevent", name])

    def shell(self, command: str) -> str:
        result = self._run_adb(["shell", command])
        return result.stdout

    def power_toggle(self) -> None:
        self.send_key(KEY_POWER_TOGGLE)

    def wake(self) -> None:
        self.send_key(KEY_WAKE)

    def sleep(self) -> None:
        self.send_key(KEY_SLEEP)

    def wake_and_wait(self, delay: float = 5.0) -> None:
        self.power_toggle()
        time.sleep(delay)

    def home(self) -> None:
        self.send_key_name("KEYCODE_HOME")

    def back(self) -> None:
        self.send_key_name("KEYCODE_BACK")

    def menu(self) -> None:
        self.send_key_name("KEYCODE_MENU")

    def dpad_up(self) -> None:
        self.send_key_name("KEYCODE_DPAD_UP")

    def dpad_down(self) -> None:
        self.send_key_name("KEYCODE_DPAD_DOWN")

    def dpad_left(self) -> None:
        self.send_key_name("KEYCODE_DPAD_LEFT")

    def dpad_right(self) -> None:
        self.send_key_name("KEYCODE_DPAD_RIGHT")

    def select(self) -> None:
        self.send_key_name("KEYCODE_DPAD_CENTER")

    def volume_up(self) -> None:
        self.send_key_name("KEYCODE_VOLUME_UP")

    def volume_down(self) -> None:
        self.send_key_name("KEYCODE_VOLUME_DOWN")

    def mute(self) -> None:
        self.send_key_name("KEYCODE_VOLUME_MUTE")

    def show_input_selector(self) -> None:
        """
        Show the input selection overlay.
        """
        self.send_key_name("KEYCODE_TV_INPUT")

    def hdmi1(self) -> None:
        self.send_key(KEY_HDMI1)

    def hdmi2(self) -> None:
        self.send_key(KEY_HDMI2)

    def hdmi3(self) -> None:
        self.send_key(KEY_HDMI3)

    def launch_app(self, package_name: str) -> None:
        self._run_adb([
            "shell", "monkey",
            "-p", package_name,
            "-c", "android.intent.category.LAUNCHER",
            "1"
        ])

    def list_running_activities(self) -> str:
        return self.shell("dumpsys activity activities | grep -i 'top-activity'")

    def screenshot(self, remote_path: str = "/sdcard/firetvscreen.png",
                   local_path: Optional[str] = None) -> Optional[str]:
        # Capture on device
        self._run_adb(["shell", "screencap", "-p", remote_path])

        if local_path is not None:
            # Pull to local machine
            result = self._run_adb(["pull", remote_path, local_path])
            print(result.stdout.strip() or result.stderr.strip())
            return local_path

        return remote_path

def switch_to_hdmi2():
    tv = FireTvController()
    print(f"Connecting to {tv.target}...")
    if not tv.connect():
        print("Could not connect. Make sure ADB debugging is enabled on the TV and IP is correct.")
        return
    if not tv.is_connected():
        print("Device not listed in adb devices, aborting.")
        return
    print("Connected!")
    tv.hdmi2()

    print("Disconnecting...")
    tv.disconnect()

def morning_turn_on():
    tv = FireTvController()

    print(f"Connecting to {tv.target}...")
    if not tv.connect():
        print("Could not connect. Make sure ADB debugging is enabled on the TV and IP is correct.")
        return

    if not tv.is_connected():
        print("Device not listed in adb devices, aborting.")
        return

    print("Connected!")

    print("Waking TV and going to HDMI 2...")
    tv.wake_and_wait(delay=5)
    tv.hdmi2()

    print("Disconnecting...")
    tv.disconnect()

def night_sleep():
    tv = FireTvController()

    print(f"Connecting to {tv.target}...")
    if not tv.connect():
        print("Could not connect. Make sure ADB debugging is enabled on the TV and IP is correct.")
        return

    if not tv.is_connected():
        print("Device not listed in adb devices, aborting.")
        return

    print("Connected!")

    print("Putting TV to sleep...")
    tv.sleep()

    print("Disconnecting...")
    tv.disconnect()

def main():
    tv = FireTvController()

    print(f"Connecting to {tv.target}...")
    if not tv.connect():
        print("Could not connect. Make sure ADB debugging is enabled on the TV and IP is correct.")
        return

    if not tv.is_connected():
        print("Device not listed in adb devices, aborting.")
        return

    print("Connected!")

    # Simple demo sequence:
    print("Waking TV and going to HDMI 2...")
    tv.wake_and_wait(delay=5)
    tv.hdmi2()

    print("Launching Netflix (if installed)...")
    tv.launch_app("com.netflix.ninja")

    time.sleep(5)
    print("Taking screenshot to firetv.png...")
    tv.screenshot(local_path="firetv.png")

    print("Putting TV to sleep in 5 seconds...")
    time.sleep(5)
    tv.sleep()

    print("Disconnecting...")
    tv.disconnect()


#if __name__ == "__main__":
 #   morning_turn_on()

#night_sleep()
#morning_turn_on()