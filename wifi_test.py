import network
import time

SSID = "Riverbank Resident WiFi"
PASSWORD = "raft726lazy"

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)

    print("Connecting to Wi-Fi...")
    for _ in range(20):
        if wlan.isconnected():
            print("Connected!")
            print("IP:", wlan.ifconfig()[0])
            return True
        time.sleep(0.5)

    print("Connection failed.")
    return False

connect_wifi()
