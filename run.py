from app import app
import sys
import os
import ipaddress

def generate_self_signed_cert(cert_file='cert.pem', key_file='key.pem'):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    import socket

    hostname = socket.gethostname()
    try:
        local_ip = ipaddress.ip_address(
            socket.gethostbyname(hostname)
        )
    except ValueError:
        local_ip = ipaddress.ip_address("127.0.0.1")

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ID"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BTS Security"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(local_ip),
        ]),
        critical=False,
    ).sign(key, hashes.SHA256())
    
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    
    return cert_file, key_file

if __name__ == '__main__':
    use_https = '--https' in sys.argv
    
    if use_https:
        cert_file = 'cert.pem'
        key_file = 'key.pem'
        
        if not (os.path.exists(cert_file) and os.path.exists(key_file)):
            print('Generating self-signed SSL certificate...')
            try:
                generate_self_signed_cert(cert_file, key_file)
                print(f'Certificate generated: {cert_file}, {key_file}')
            except Exception as e:
                print(f'Failed to generate certificate: {e}')
                print('Install pyopenssl and cryptography for HTTPS support.')
                sys.exit(1)
        
        print(f'Starting HTTPS server on https://0.0.0.0:5000')
        print('Note: Browser will show security warning for self-signed certificate.')
        print('Click "Advanced" -> "Proceed to localhost" to access.')
        app.run(
            debug=True,
            host='0.0.0.0',
            port=5000,
            ssl_context=(cert_file, key_file)
        )
    else:
        app.run(
            debug=True,
            host='0.0.0.0',
            port=5000
        )
