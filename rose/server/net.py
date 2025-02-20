import logging, os

from twisted.internet import protocol
from twisted.protocols import basic
from twisted.web import http, resource, xmlrpc

from autobahn.twisted.websocket import WebSocketServerFactory
from autobahn.twisted.websocket import WebSocketServerProtocol

from rose.common import error, message

log = logging.getLogger('net')


class Hub(object):

    def __init__(self, game):
        game.hub = self
        self.game = game
        self.clients = set()

    # PlayerProtocol hub interface

    def add_player(self, player):
        # First add player, will raise if there are too many players or this
        # name is already taken.
        self.game.add_player(player.name)
        self.clients.add(player)

    def remove_player(self, player):
        if player in self.clients:
            self.clients.remove(player)
            self.game.remove_player(player.name)

    def drive_player(self, player, info):
        self.game.drive_player(player.name, info)

    # WatcherProtocol hub interface

    def add_watcher(self, watcher):
        self.clients.add(watcher)
        game_state = message.Message("update", self.game.state())
        game_history = message.Message('history', {'todo': True})
        watcher.send_message(bytes(str(game_state), 'utf-8'))
        watcher.send_message(bytes(str(game_history), 'utf-8'))

    def remove_watcher(self, watcher):
        self.clients.discard(watcher)

    # Game hub interface

    def broadcast(self, msg):
        data = bytes(str(msg), 'utf-8')
        for client in self.clients:
            client.send_message(data)


class PlayerProtocol(basic.LineReceiver):

    def __init__(self, hub):
        self.hub = hub
        self.name = None

    # LineReceiver interface

    def connectionLost(self, reason):
        self.hub.remove_player(self)

    def lineReceived(self, line):
        try:
            msg = message.parse(line)
            self.dispatch(msg)
        except error.Error as e:
            log.warning("Error handling message: %s", e)
            msg = message.Message('error', {'message': str(e)})
            self.sendLine(bytes(str(msg), 'utf-8'))
            self.transport.loseConnection()

    # Hub client interface

    def send_message(self, data):
        self.sendLine(data)

    # Disaptching messages

    def dispatch(self, msg):
        if self.name is None:
            # New player
            if msg.action != 'join':
                raise error.ActionForbidden(msg.action)
            if 'name' not in msg.payload:
                raise error.InvalidMessage("name required")
            self.name = msg.payload['name']
            self.hub.add_player(self)
        else:
            # Registered player
            if msg.action == 'drive':
                self.hub.drive_player(self, msg.payload)
            else:
                raise error.ActionForbidden(msg.action)


class PlayerFactory(protocol.ServerFactory):

    def __init__(self, hub):
        self.hub = hub

    def buildProtocol(self, addr):
        p = PlayerProtocol(self.hub)
        p.factory = self
        return p


class WatcherProtocol(WebSocketServerProtocol):

    def __init__(self, hub):
        super().__init__()
        self.hub = hub

    # WebSocketServerProtocol interface

    def onConnect(self, request):
        log.info("watcher connected from %s", request)

    def onOpen(self):
        self.hub.add_watcher(self)

    def onClose(self, wasClean, code, reason):
        log.info("watcher closed (wasClean=%s, code=%s, reason=%s)",
                 wasClean, code, reason)
        self.hub.remove_watcher(self)

    # Hub client interface
    def onMessage(self, payload, isBinary):
        self.sendMessage(payload, isBinary)

    def send_message(self, data):
        self.sendMessage(data, False)


class WatcherFactory(WebSocketServerFactory):

    def __init__(self, url, hub):
        super().__init__(url)
        self.hub = hub
        #WebSocketServerFactory.__init__(self, url)

    def buildProtocol(self, addr):
        p = WatcherProtocol(self.hub)
        p.factory = self
        return p


class CliAdmin(xmlrpc.XMLRPC):

    def __init__(self, game):
        self.game = game
        xmlrpc.XMLRPC.__init__(self, allowNone=True)

    def xmlrpc_start(self):
        try:
            self.game.start()
        except error.GameAlreadyStarted as e:
            raise xmlrpc.Fault(1, str(e))

    def xmlrpc_stop(self):
        try:
            self.game.stop()
        except error.GameNotStarted as e:
            raise xmlrpc.Fault(1, str(e))

    def xmlrpc_set_rate(self, rate):
        self.game.rate = rate

class ScoreRecorder(resource.Resource):
    def __init__(self):
        super().__init__()
    def render_POST(self, request):
        if b'name' in request.args and b'score' in request.args:
            name = request.args[b'name'][0].decode()
            score = request.args[b'score'][0].decode()
            open('highest_score.txt', 'w+', encoding = 'utf8').write(f'{name},{score}')
            return b''
        if b'fetch' in request.args:
            if not os.path.isfile('highest_score.txt'):
                return bytes('', 'utf-8')
            return bytes(open('highest_score.txt').read(), 'utf-8')

class WebAdmin(resource.Resource):

    def __init__(self, game):
        self.game = game
        super().__init__()

    def render_POST(self, request):
        if b"running" in request.args:
            value = request.args[b"running"][0]
            if value == b"1":
                self.game.start()
            elif value == b"0":
                if self.game.started:
                    self.game.stop()
            else:
                request.setResponseCode(http.BAD_REQUEST)
                return bytes(f"Invalid running value {value}, expected (1, 0)", 'utf-8')
        if b"rate" in request.args:
            value = request.args[b"rate"][0].decode()
            try:
                self.game.rate = float(value)
            except ValueError:
                request.setResponseCode(http.BAD_REQUEST)
                return bytes(f"Invalid rate value {value}, expected number", 'utf-8')
        return b""
