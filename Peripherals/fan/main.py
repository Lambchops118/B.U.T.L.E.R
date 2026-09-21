# Part of Butler
# Monkey Butler Device Operations System

# Uses MQTT to turn fan on or off. Pin 16 on RPI Pico W

import time
import network
import machine
from   umqtt.simple import MQTTClient

# Configuration
WIFI_SSID         = 'Verizon_4VLXXY'
WIFI_PASSWORD     = 'artery4-fob-vim'
MQTT_BROKER       = '192.168.1.160'
MQTT_CLIENT_ID    = 'pico-w-client'
MQTT_TOPIC_PREFIX = b'fan/'
CONTROL_TOPICS    = [MQTT_TOPIC_PREFIX + bytes(str(pin), 'utf-8') for pin in [16]]

# Pin setup
PIN_NUMBERS       = [16]
PINS              = {pin_num: machine.Pin(pin_num, machine.Pin.OUT) for pin_num in PIN_NUMBERS}

print('started')

# Wi-Fi connection
def connect_wifi():
    print("Connecting to wifi...")
    wlan = network.WLAN(network.STA_IF)
    print("wlan: " + str(wlan))
    wlan.active(True)
    if not wlan.isconnected():
        print("wlan not connected...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        print("WIFI_SSID: " + WIFI_SSID)
        print("WIFI_PASSWORD: " + WIFI_PASSWORD)
        while not wlan.isconnected():
            print("Sleeping...")
            time.sleep(0.5)
    print('Connected to Wi-Fi:', wlan.ifconfig())

# MQTT message callback
def mqtt_callback(topic, msg):
    try:
        pin_number = int(topic.decode().split('/')[-1])
        if pin_number in PINS:
            pin = PINS[pin_number]
            if msg == b'1':
                pin.value(0) # turn on
            elif msg == b'0':
                pin.value(1) # turn off
            else:
                print(f"Invalid command: {msg}")
                return
            # Publish new status
            status_topic = b'status/' + bytes(str(pin_number), 'utf-8')
            print(status_topic)
            client.publish(status_topic, str(pin.value()))
    except Exception as e:
        print('Error handling message:', e)

# Connect Wi-Fi
connect_wifi()

# Setup MQTT
client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER)

print("MQTT_CLIENT_ID: " + MQTT_CLIENT_ID)
print("MQTT_BROKER: " + MQTT_BROKER)

client.set_callback(mqtt_callback)
client.connect()
for topic in CONTROL_TOPICS:
    client.subscribe(topic)
    print("Subscribed to: " + str(topic))

# Main loop
try:
    while True:
        client.check_msg()
        time.sleep(0.1)
except KeyboardInterrupt:
    client.disconnect()