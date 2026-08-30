#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
for pkg in garak textattack modelscan fickling agentic-security; do
  printf '%-24s ' "$pkg"
  r=$(timeout 300 pipx install "$pkg" 2>&1)
  if echo "$r" | grep -qi "installed package"; then echo "OK  $(echo "$r"|grep -oiE 'installed package [^,]+'|head -1)"
  elif echo "$r" | grep -qi "no apps"; then echo "NO APPS (pipx refuses)"
  elif echo "$r" | grep -qi "already seems to be installed"; then echo "OK (already installed)"
  else echo "FAIL: $(echo "$r"|grep -iE 'error|fatal'|head -1)"; fi
done
echo "--- npm promptfoo ---"
timeout 300 npm install -g promptfoo >/dev/null 2>&1 && which promptfoo && promptfoo --version 2>&1|head -1 || echo "npm route failed"
