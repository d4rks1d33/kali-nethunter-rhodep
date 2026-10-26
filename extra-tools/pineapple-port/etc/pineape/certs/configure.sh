#!/bin/bash
# configure.sh -- write the CA/server/client subject fields into the .cnf files.
#
# Ported from the original WiFi Pineapple (/etc/pineape/certs/configure.sh) to
# Kali/NetHunter (GNU sed, OpenSSL 3.x). The panel calls it exactly like the
# original (PineAP/api/module.php:568):
#   configure.sh -p <pass> -c <country> -s <state> -l <loc> -o <org> -e <mail> -n <cn>
# then runs bootstrap.sh to actually generate the certs.
#
# FIX vs original: the upstream script used `sed -i '/dev/null' -e ...`, where
# `'/dev/null'` was (incorrectly) taken as the in-place backup suffix. On GNU
# sed that creates junk backup files / can error. We use a proper `sed -i` with
# no suffix, and clean any stray *.bak at the end (as the original intended).

cd "$(dirname "$0")" || exit 1

# Sane defaults (identical to the original).
password="pineapplesareyummy"
country="US"
state="California"
locality="Oakland"
organization="Hak5 LLC"
email="bounce@hak5.org"
commonname="WiFi Pineapple Cetificate Authority"

# setconfigopt KEY VAL [FILE LINE]
#   no FILE/LINE -> apply to ca.cnf client.cnf server.cnf (all)
#   FILE LINE    -> apply only to that line of that file
setconfigopt() {
    key=$1
    val=$2
    file=$3
    line=$4
    if [[ -z $line || -z $file ]]; then
        for f in ca.cnf client.cnf server.cnf; do
            sed -i -e 's/\('"$key"'[[:space:]]*\=[[:space:]]*\).*\s*/\1'"$val"'/g' "$f"
        done
    else
        sed -i -e "$line"'s/\('"$key"'[[:space:]]*\=[[:space:]]*\).*\s*/\1'"$val"'/g' "$file"
    fi
}

while getopts ":p:c:s:l:o:e:n:" opt; do
  case ${opt} in
    p ) password=$OPTARG ;;
    c ) country=$OPTARG ;;
    s ) state=$OPTARG ;;
    l ) locality=$OPTARG ;;
    o ) organization=$OPTARG ;;
    e ) email=$OPTARG ;;
    n ) commonname=$OPTARG ;;
    \? ) echo "Invalid option: $OPTARG" 1>&2 ;;
    : ) echo "Invalid option: $OPTARG requires an argument" 1>&2 ;;
  esac
done
shift $((OPTIND -1))

# restore pristine .cnf before applying (idempotent).
./reset.sh

echo "[-] Password (-p): $password"
echo "[-] Country (-c): $country"
echo "[-] State/province (-s): $state"
echo "[-] Locality (-l): $locality"
echo "[-] Organization (-o): $organization"
echo "[-] Email (-e): $email"
echo "[-] Common name (-n): $commonname"

echo "[*] Writing configuration"
setconfigopt "input_password" "$password"
setconfigopt "output_password" "$password"
setconfigopt "countryName" "$country" ca.cnf 50
setconfigopt "countryName" "$country" server.cnf 48
setconfigopt "countryName" "$country" client.cnf 48
setconfigopt "stateOrProvinceName" "$state" ca.cnf 51
setconfigopt "stateOrProvinceName" "$state" server.cnf 49
setconfigopt "stateOrProvinceName" "$state" client.cnf 49
setconfigopt "localityName" "$locality" ca.cnf 52
setconfigopt "localityName" "$locality" server.cnf 50
setconfigopt "localityName" "$locality" client.cnf 50
setconfigopt "organizationName" "$organization" ca.cnf 53
setconfigopt "organizationName" "$organization" server.cnf 51
setconfigopt "organizationName" "$organization" client.cnf 51
setconfigopt "emailAddress" "$email" ca.cnf 54
setconfigopt "emailAddress" "$email" server.cnf 52
setconfigopt "emailAddress" "$email" client.cnf 52
setconfigopt "commonName" "$commonname" ca.cnf 55
setconfigopt "commonName" "$commonname" server.cnf 53
setconfigopt "commonName" "$commonname" client.cnf 53
echo "[+] Configuration complete"

rm -f *.bak
