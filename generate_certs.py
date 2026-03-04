#!/usr/bin/env python3
"""
Generate TLS certificates for Office Hours relay server.

Usage:
    python generate_certs.py                    # Generate CA + server cert for localhost
    python generate_certs.py --domain ohinter.com  # Generate for a specific domain
    python generate_certs.py --output /path/to/certs  # Custom output directory

Outputs:
    ca.pem          - CA certificate (distribute to clients for self-signed mode)
    server_cert.pem - Server certificate (use with --cert on relay_server.py)
    server_key.pem  - Server private key (use with --key on relay_server.py)
"""

import argparse
import datetime
import os
import sys

def generate_certs(domain="localhost", output_dir="."):
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        print("Error: 'cryptography' package is required.")
        print("Install it with: pip install cryptography")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # ── Step 1: Generate CA ──────────────────────────────────────
    print(f"Generating CA certificate...")
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Office Hours CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Office Hours"),
    ])

    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_path = os.path.join(output_dir, "ca.pem")
    with open(ca_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    print(f"  CA certificate: {ca_path}")

    # ── Step 2: Generate Server Cert ─────────────────────────────
    print(f"Generating server certificate for '{domain}'...")
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, domain),
    ])

    # Build SAN list
    san_names = [x509.DNSName(domain)]
    if domain == "localhost":
        import ipaddress
        san_names.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
        san_names.append(x509.DNSName("*.local"))

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path = os.path.join(output_dir, "server_cert.pem")
    with open(cert_path, "wb") as f:
        f.write(server_cert.public_bytes(serialization.Encoding.PEM))
    print(f"  Server certificate: {cert_path}")

    key_path = os.path.join(output_dir, "server_key.pem")
    with open(key_path, "wb") as f:
        f.write(server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    os.chmod(key_path, 0o600)
    print(f"  Server private key: {key_path}")

    # ── Summary ──────────────────────────────────────────────────
    print()
    print("Done! To use these certificates:")
    print()
    print("  Relay server:")
    print(f"    python relay_server.py --cert {cert_path} --key {key_path}")
    print()
    print("  Clients (self-signed mode):")
    print(f"    export TALKBACK_RELAY_CA_CERT={ca_path}")
    print(f"    export TALKBACK_RELAY_TLS=1")
    print()
    print("  Or for Let's Encrypt (no CA file needed):")
    print(f"    export TALKBACK_RELAY_TLS=1")
    print(f"    # Clients use system trust store automatically")


def main():
    parser = argparse.ArgumentParser(description="Generate TLS certificates for Office Hours")
    parser.add_argument("--domain", default="localhost", help="Server domain name (default: localhost)")
    parser.add_argument("--output", default=".", help="Output directory (default: current directory)")
    args = parser.parse_args()

    generate_certs(domain=args.domain, output_dir=args.output)


if __name__ == "__main__":
    main()
