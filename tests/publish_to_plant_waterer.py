import paho.mqtt.client as mqtt

def publish_pump_state(broker_ip, message):
    """
    Publish b'0' or b'1' to topic 'waterer_pump/2'.

    Args:
        broker_ip (str): IP address of the MQTT broker.
        message (bytes): Must be b'0' or b'1'.
    """
    if message not in [b'0', b'1']:
        raise ValueError("message must be b'0' or b'1'")

    client = mqtt.Client()
    client.connect(broker_ip, 1883, 60)
    client.publish("quad_pump/19", message)
    client.disconnect()

publish_pump_state("192.168.1.160", b'1')
print("done")


