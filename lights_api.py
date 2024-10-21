from flask import Flask, jsonify, request
from controller import LightsController

lights_controller = LightsController()
app = Flask(__name__)

@app.route('/set_color', methods=['POST'])
def set_color():
    data = request.json
    color = data['color']
    lights_controller.set_light_color(color)
    return jsonify(message="color set")

@app.route('/set_brightness', methods=['POST'])
def set_brightness():
    data = request.json
    lights_controller.set_brightness(data['brightness'])
    return jsonify(message="brightness set")

@app.route('/turn_on', methods=['GET'])
def turn_on():
    lights_controller.turn_on()
    return jsonify(message="turned on")

@app.route('/turn_off', methods=['GET'])
def turn_off():
    lights_controller.turn_off()
    return jsonify(message="turned ff")

if __name__ == '__main__':
    app.run(host='0.0.0.0')
