import os
import ssl
from app import create_app

app = create_app()

if __name__ == '__main__':
    cert = os.path.join(os.path.dirname(__file__), 'data', 'tls', 'cert.pem')
    key = os.path.join(os.path.dirname(__file__), 'data', 'tls', 'key.pem')

    if os.path.exists(cert) and os.path.exists(key):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, ssl_context=context)
    else:
        app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
