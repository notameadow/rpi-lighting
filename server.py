import os
import ssl
import threading
from app import create_app

app = create_app()

if __name__ == '__main__':
    cert = os.path.join(os.path.dirname(__file__), 'data', 'tls', 'cert.pem')
    key = os.path.join(os.path.dirname(__file__), 'data', 'tls', 'key.pem')

    if os.path.exists(cert) and os.path.exists(key):
        # Run HTTPS on 5443 in a background thread
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)

        def run_https():
            app.run(host='0.0.0.0', port=5443, debug=False, threaded=True, ssl_context=context)

        t = threading.Thread(target=run_https, daemon=True)
        t.start()

    # HTTP on 5001 (always available, never freezes)
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
