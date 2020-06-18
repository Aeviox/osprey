import io
import socket
from requests import get
import threading


class Base:
    """
    Base class from which all others inherit.
    Implements basic logging functionality
    """
    def __init__(self, debug=False):
        self.debug_mode = debug  # Whether debug mode is active
        self.exit = False        # Used to exit program and handle errors

    def log(self, message, cause=None, level='log'):
        """ Outputs a message according to debug level """
        if level == 'log':  # always show message
            print("> {}".format(message))
        elif level == 'status':  # always show as important message
            print("[{}]".format(message))
        elif level == 'error':  # always show as error
            print("[ERROR]: {}".format(message))
            print("[THREAD]: {}".format(threading.currentThread().getName()))
            if cause:
                print("[CAUSE]: {}".format(cause))  # show cause if given
        elif level == 'debug' and self.debug_mode:  # only show in debug mode
            # (debug) [thread_name]: message content
            print("(debug) [{}]: {}".format(threading.currentThread().getName(), message))

    def debug(self, message):
        """ Sends a debug level message """
        self.log(message, level='debug')

    def error(self, message, cause=None):
        """ Throw error and signal to disconnect """
        self.log(message, cause=cause, level='error')
        self.exit = True


class ConnectionBase(Base):
    """
    Object the represents a single connection.
    Holds the socket and associated buffers.
    Runs on it's own thread in the Stream class.
    setup() is overwritten by the ServerConnection and ClientConnection classes
    """
    def __init__(self, ip, port, debug):
        super().__init__(debug)

        self.thread = None  # thread that this connection will run on

        self.host = Address(ip, port)  # address of server
        self.client = None     # address of client
        self.socket = None     # socket object to read/write to

        self.buffer = b''        # incoming stream buffer to read from
        self.header_buffer = []  # outgoing header buffer

        # variables for each incoming request
        self.method = None   # HTTP request method
        self.path = None     # HTTP request path
        self.version = None  # HTTP request version
        self.header = {}     # header dictionary
        self.content = None  # content received

        self.encoding = 'iso-8859-1'  # encoding for data stream

    def setup(self):
        """
        Overwritten by server and client.
        Initializes socket objects and connections.
        """

    def stream(self):
        """
        Overwritten by Server and Client.
        Main method called after class instance creation.
        Must run on it's own thread.
        Continually reads/writes to file streams.
        """
        pass

    def finish(self):
        """
        Overwritten in Server and Client.
        Execute any last processes before termination.
        """
        pass

    def handle(self):
        """ Receive, parse, and handle a single request as it is streamed """
        try:
            data = self.socket.recv(4096)  # receive data from pi (size arbitrary?)
        except BlockingIOError:  # Temporarily unavailable (errno EWOULDBLOCK)
            pass
        else:
            if data:  # if received data
                self.buffer += data  # append to data buffer
                # TODO: Implement max buffer size? Will need to be well over JPEG image size (~100,000)
            else:  # stream disconnected
                self.log("Client Disconnected {}:{}".format(self.pi_ip, self.pi_port))
                self.exit = True  # signal to disconnect
                return

        if not self.method:  # request-line not yet received
            self.parse_request_line()
        elif not self.header:  # header not yet received
            self.parse_header()
        elif not self.content:  # content not yet received
            self.parse_content()
        else:  # all parts received
            if not hasattr(self, self.method):  # if a method for the request doesn't exist
                self.error('Unsupported Method', self.method)
                return
            method_func = getattr(self, self.method)  # get class method that matches name of request method
            method_func()  # call it to handle the request
            self.reset()  # reset all request variables

    def read(self, length, line=False, decode=True):
        """
        If line is False:
            - Reads exactly <length> amount from stream.
            - Returns everything including any whitespace
        If line is True:
            - Read from stream buffer until CLRF encountered
            - Returns single decoded line without the trailing CLRF
            - Returns '' if the line received was itself only a CLRF (blank line)
            - Returns None if whole line has not yet been received (buffer not at <length> yet)
            - if no CLRF before <length> reached, stop reading and throw error
        Decode specifies whether to decode the data from bytes to string. If true, also strip whitespace
        """
        if not line:  # exact length specified
            if len(self.buffer) < length:  # not enough data received to read this amount
                return
            data = self.buffer[:length]
            self.buffer = self.buffer[length:]  # move down the buffer
            if decode:
                return data.decode(self.encoding)  # slice exact amount
            else:
                return data

        # else, grab a single line, denoted by CLRF
        CLRF = "\r\n".encode(self.encoding)
        loc = self.buffer.find(CLRF)  # find first CLRF
        if loc > length or (loc == -1 and len(self.buffer) > length):  # no CLRF found before max length reached
            self.error("buffer too long before CLRF (max length: {})".format(length), self.buffer.decode(self.encoding))
            return
        elif loc == -1:  # CLRF not found, but we may just have not received it yet.
            return

        line = self.buffer[:loc+2]  # slice including CLRF
        self.buffer = self.buffer[loc+2:]  # move buffer past CLRF
        if decode:
            return line.decode(self.encoding).strip()  # decode and strip whitespace including CLRF
        else:
            return line

    def parse_request_line(self):
        """ Parses the Request-line of an HTTP request, finding the request method, path, and version strings """
        max_len = 1024  # max length of request-line before error (arbitrary choice)

        line = self.read(max_len, line=True)  # read first line from stream
        if line is None:  # nothing yet
            return
        self.log("Received Request-Line: '{}'".format(line), level='debug')

        words = line.split()
        if len(words) != 3:
            err = "Request-Line must conform to HTTP Standard (METHOD /path HTTP/X.X\\r\\n)"
            self.error(err, line)
            return

        self.method = words[0]
        self.path = words[1]
        self.version = words[2]

    def parse_header(self):
        """ Fills the header dictionary from the received header text """
        max_num = 32    # max number of headers (arbitrary choice)
        max_len = 1024  # max length of headers (arbitrary choice)
        for _ in range(max_num):
            line = self.read(max_len, line=True)  # read next line in stream
            if line is None:  # nothing yet
                return
            if line == '':  # empty line signaling end of headers
                self.log("All headers received", level='debug')
                break
            key, val = line.split(':', 1)  # extract field and value by splitting at first colon
            self.header[key] = val.strip()  # remove extra whitespace from value
            self.log("Received Header '{}':{}".format(key, val), level='debug')
        else:
            self.error("Too many headers", "> {}".format(max_num))

    def parse_content(self):
        """ Parse request payload, if any """
        length = self.header.get("content-length")
        if length:  # if content length was sent
            content = self.read(int(length), decode=False)  # read raw bytes from stream
            if content:
                self.content = content
                self.log("Received Content of length: {}".format(len(self.content)), level='debug')
            else:  # not yet fully received
                return
        else:  # no content length specified - assuming no content sent
            self.content = True  # mark content as 'received'

    def reset(self):
        """ Resets variables associated with a single request """
        self.method = None
        self.path = None
        self.version = None
        self.header = {}
        self.content = None
        self.log("Reset request variables", level='debug')

    def send_request_line(self, method, path='/', version='HTTP/1.1'):
        """ sends an HTTP request line to the stream """
        line = "{} {} {}\r\n".format(method, path, version)
        self.socket.sendall(line.encode(self.encoding))
        self.log("Sent request line '{}'".format(line), level='debug')

    def add_header(self, keyword, value):
        """ add a MIME header to the headers buffer. Does not send to stream. """
        text = "{}: {}\r\n".format(keyword, value)   # text to be sent
        data = text.encode(self.encoding, 'strict')  # convert text to bytes
        self.header_buffer.append(data)              # add to buffer
        self.log("Added header '{}':{}".format(keyword, value), level='debug')
        '''
        if keyword.lower() == 'connection':
            if value.lower() == 'close':
                self.close_connection = True
            elif value.lower() == 'keep-alive':
                self.close_connection = False
        '''

    def send_headers(self):
        """ Adds a blank line ending the MIME headers, then sends the header buffer to the stream """
        self.header_buffer.append(b"\r\n")              # append blank like
        self.socket.sendall(b"".join(self.header_buffer))  # combine all headers and send
        self.header_buffer = []                         # clear header buffer
        self.log("Sent headers", level='debug')

    def send_content(self, content):
        """ Sends content to the stream """
        if type(content) == str:
            data = content.encode(self.encoding)
        elif type(content) == bytes:
            data = content
        else:
            self.error("Content format not accounted for", type(content))
            return
        self.socket.sendall(content)
        self.log("Sent content of length: {}".format(len(content)), level='debug')

    def add_response(self, code):
        """ Sends a response line back. Must be sent with send_headers()"""
        version = "HTTP/1.1"
        message = 'MESSAGE'
        response_line = "{} {} {}\r\n".format(version, code, message)

        self.log("Added response headers with code {}".format(code), level='debug')
        self.header_buffer.append(response_line.encode(self.encoding))
        self.add_header('Server', 'BaseHTTP/0.6 Python/3.7.3')
        self.add_header('Date', 'Thu, 18 Jun 2020 16:05:22 GMT')  # placeholders

    def close(self):
        """ Closes the connection """
        self.socket.close()
        self.log("Connection Closed")


class ServerConnection(ConnectionBase):
    def setup(self):
        """ Create socket and bind to local address then wait for connection from a client """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # AF_INET = IP, SOCK_STREAM = TCP
        try:  # Bind socket to ip and port
            # self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # allow socket to reuse address
            sock.bind(self.host.tup)  # bind to host address
            self.debug("Socket Bound to {}".format(self.host))
        except Exception as e:
            self.error("Failed to bind socket to {}".format(self.host), e)

        try:  # Listen for connection
            self.debug("Listening for Connection...")
            sock.listen()
        except Exception as e:
            self.error("While listening on {}".format(self.host), e)

        try:  # Accept connection. Accept() returns a new socket object that can send and receive data.
            self.socket, (ip, port) = sock.accept()
            self.client = Address(ip, port)
            self.debug("Accepted Socket Connection From {}".format(self.client))
        except Exception as e:
            self.error("Failed to accept connection from {}".format(self.client), e)

        self.log("New Connection From: {}".format(self.client))
        # if self.timeout is not None:  # is this needed?
        #    self.socket.settimeout(self.timeout)


class ClientConnection(ConnectionBase):
    def setup(self):
        """ Create socket and connect to a server ip """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # AF_INET = IP, SOCK_STREAM = TCP
        try:  # connect socket to given address
            self.debug("Attempting to connect to {}".format(self.host))
            self.socket.connect(self.host.tup)
            self.debug("Socket Connected")
        except Exception as e:
            self.error("Failed to connect to server", e)


class Stream:
    """
    Base class for both Server and Client classes.
    Provides the ability to send and read HTTP requests.
    Methods that are overwritten in Server and Client classes:
        - setup(): initializes socket connection
        - stream(): continually performs read/write action to TCP stream
        - finish(): executes before termination of connection
    """
    def __init__(self, ip, port, debug=False):
        self.pi_ip = None    # RasPi ip
        self.pi_port = None  # RasPi port

        self.in_sockets = []      # sockets for receiving data streams
        self.out_sockets = []     # sockets for sending data streams

        # misc.
        self.frames_sent = 0      # number of frames sent to server (Sent in header)
        self.frames_received = 0  # number of frames received by server

    def serve(self):
        """ Start the server """
        self.log("IP: {}".format(get('http://ipinfo.io/ip').text.strip()))  # show this machine's public ip
        try:
            self.setup()   # create and connect/bind sockets
            self.stream()  # main streaming loop
        except ConnectionResetError:
            self.log("Server Disconnected")
        except KeyboardInterrupt:
            self.log("Manual Termination", level='status')
        finally:
            self.finish()  # final executions
            self.close()   # close server


class Address:
    """
    Basic class to display ip addresses with port number.
    I got tired of formatting strings.
    """
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        if self.ip in ['', '*', '0.0.0.0']:
            self.ip = ''
        self.rep = '{}:{}'.format(self.ip, self.port)  # string representation
        self.tup = (self.ip, self.port)  # tuple of (ip, port)

    def __repr__(self):
        return self.rep


class FrameBuffer(object):
    """
    Object used as a buffer containing a single frame.
    Can be written to by the picam.
    """
    def __init__(self):
        self.frame = None
        self.buffer = io.BytesIO()
        self.condition = Condition()

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # New frame, copy the existing buffer's content
            self.buffer.truncate()
            with self.condition:
                self.frame = self.buffer.getvalue()
                self.condition.notify_all()  # notify all clients that it's available
            self.buffer.seek(0)
        return self.buffer.write(buf)