#!/usr/bin/env bash
# Inspector smoke test: the official MCP Inspector (pinned) lists lumen-read's tools over stdio,
# once per protocol era. The servers must answer both: "modern" (2026-07-28, server/discover) and
# "legacy" (the initialize handshake). Each run must list exactly 3 tools.
#
#   bash mcp/scripts/inspector_smoke.sh
#
# Exit 0 = both eras listed 3 tools ("PASS") or the check could not run ("SKIP", said loudly:
# no Node, or npx could not fetch the pinned Inspector). Exit 1 = it ran and a check failed.
# Needs Node 22.19+ and the venv at .venv-mcp/. Makes no Claude API call.
set -u
INSPECTOR="@modelcontextprotocol/inspector@2.12.0"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${ROOT}/.venv-mcp/bin/python"
SERVER="${ROOT}/mcp/python/read_server.py"
EXPECT=3

skip() { echo "SKIP: $1 (nothing was checked; this is not a pass)"; exit 0; }

command -v node >/dev/null 2>&1 || skip "node is not installed"
command -v npx >/dev/null 2>&1 || skip "npx is not installed"
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&b>=19)?0:1)' \
  || skip "Node $(node -v) is older than 22.19"
[ -x "${PY}" ] || { echo "FAIL: ${PY} not found; create the venv first (see mcp/requirements.txt)"; exit 1; }

# Can npx get the pinned package? A network or npm failure here is a skip, not a pass or a fail.
if ! got="$(npx -y "${INSPECTOR}" --version 2>&1)"; then
  skip "npx could not fetch ${INSPECTOR}: $(echo "${got}" | tail -1)"
fi
echo "Inspector ${got}"

status=0
for era in modern legacy; do
  if ! out="$(npx -y "${INSPECTOR}" --cli --protocol-era "${era}" "${PY}" "${SERVER}" --method tools/list 2>&1)"; then
    echo "FAIL (${era}): Inspector exited non-zero"; echo "${out}" | tail -5; status=1; continue
  fi
  n="$(echo "${out}" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{console.log(JSON.parse(s).tools.length)}catch(e){console.log("unparseable")}})')"
  if [ "${n}" = "${EXPECT}" ]; then
    echo "ok   (${era}): tools/list returned ${n} tools"
  else
    echo "FAIL (${era}): expected ${EXPECT} tools, got ${n}"; status=1
  fi
done

[ "${status}" -eq 0 ] && echo "PASS: both protocol eras list ${EXPECT} tools"
exit "${status}"
