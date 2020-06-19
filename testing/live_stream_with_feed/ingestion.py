import json
from ingestion_lib import Server

# get configured settings
with open('config.json') as file:
    config = json.load(file)

port = config['PORT']

server = Server(port, debug=True)
server.serve()

