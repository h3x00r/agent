for f in $(grep -lsxz "NEXUS_RUN=$1" /proc/[0-9]*/environ); do
      p="${f#/proc/}"; p="${p%/environ}"
      while read -r k v; do if [ "$k" = PPid: ] && [ "$v" = 0 ]; then kill -"$2" "-$p"; fi; done < "/proc/$p/status"
    done 2>/dev/null
