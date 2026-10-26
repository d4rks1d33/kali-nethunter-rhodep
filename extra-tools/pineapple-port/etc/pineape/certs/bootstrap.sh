#!/bin/bash
# bootstrap.sh -- generate the CA + server (+ client) certificates with OpenSSL.
#
# Ported from the original WiFi Pineapple (/etc/pineape/certs/bootstrap.sh) to
# Kali/NetHunter (OpenSSL 3.x). The panel calls it after configure.sh
# (PineAP/api/module.php:569) to produce the certs the Enterprise VAP uses:
#   ca.pem/ca.key/ca.der, server.key/server.csr/server.crt/server.p12/server.pem,
#   client.* and the index.txt/serial CA database.
#
# PORTABILITY: the original hardcoded /etc/pineape/certs. We cd into our own
# directory and use relative paths, so this works both when installed
# (/etc/pineape/certs via the install symlink) AND when tested under any prefix
# (e.g. a throwaway /tmp copy). Override CERTDIR to force a location.

umask 027
cd "$(dirname "$0")" || exit 1
CERTDIR="${CERTDIR:-$PWD}"
cd "$CERTDIR" || exit 1

getcnf() { grep "$1" "$2" | sed 's/.*=//;s/^ *//'; }

if [ ! -f random ]; then
	ln -sf /dev/urandom random
fi

if [ ! -f server.key ]; then
	openssl req -new -out server.csr -newkey rsa:2048 -nodes \
		-keyout server.key -config server.cnf
fi

if [ ! -f ca.key ]; then
	openssl req -new -x509 -keyout ca.key -out ca.pem \
		-days "$(getcnf default_days ca.cnf)" -config ca.cnf
fi

if [ ! -f index.txt ]; then
	touch index.txt
fi

if [ ! -f serial ]; then
	echo '01' > serial
fi

if [ ! -f server.crt ]; then
	openssl ca -batch -keyfile ca.key -cert ca.pem -in server.csr \
		-key "$(getcnf output_password ca.cnf)" -out server.crt \
		-extensions xpserver_ext -extfile xpextensions -config server.cnf
fi

if [ ! -f server.p12 ]; then
	openssl pkcs12 -export -in server.crt -inkey server.key -out server.p12 \
		-passin pass:"$(getcnf output_password server.cnf)" \
		-passout pass:"$(getcnf output_password server.cnf)"
fi

if [ ! -f server.pem ]; then
	openssl pkcs12 -in server.p12 -out server.pem -nodes \
		-passin pass:"$(getcnf output_password server.cnf)"
	openssl verify -CAfile ca.pem server.pem
fi

if [ ! -f ca.der ]; then
	openssl x509 -inform PEM -outform DER -in ca.pem -out ca.der
fi

if [ ! -f client.key ]; then
	openssl req -new -out client.csr -newkey rsa:2048 -nodes \
		-keyout client.key -config client.cnf
fi

if [ ! -f client.crt ]; then
	openssl ca -batch -keyfile ca.key -cert ca.pem -in client.csr \
		-key "$(getcnf output_password ca.cnf)" -out client.crt \
		-extensions xpclient_ext -extfile xpextensions -config client.cnf
fi

echo "[+] Certificate bootstrap complete in $CERTDIR"
