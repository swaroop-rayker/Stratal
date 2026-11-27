#!/bin/sh
# Spike S-11 follow-up: why the first GPU load took about 60 s, and what Ollama's
# memory is once page cache is set apart (memory.peak counts the file cache too).
# One Ollama container, no network; the model is loaded on the GPU three times in a
# row (unloaded in between), reading memory.stat's anon (process memory) and file
# (page cache) at each point. Run from the repository in Git Bash:
#
#   sh scripts/spikes/s11_followup.sh qwen3-1.7b-q4km <revision> <file>
set -eu
export MSYS_NO_PATHCONV=1
model=$1 rev=$2 file=$3
name=s11-followup
store=s11-followup
tag=s11-$model

mem() { docker exec $name sh -c "awk '/^(anon|file) /{printf \"%s=%d MiB \", \$1, \$2/1048576}' /sys/fs/cgroup/memory.stat; echo"; }

docker rm -f $name >/dev/null 2>&1 || true
docker volume create $store >/dev/null
docker run -d --name $name --gpus all --network none --memory 4g -e OLLAMA_NO_CLOUD=1 \
  -v stratal_stratal-data:/data:ro -v $store:/root/.ollama ollama/ollama:0.40.16 >/dev/null
sleep 4
echo "started:      $(mem)"
docker exec $name sh -c "printf 'FROM %s\nPARAMETER num_ctx 4096\n' '/data/models/$model/$rev/$file' > /tmp/Modelfile && ollama create $tag -f /tmp/Modelfile" >/dev/null 2>&1
echo "after create: $(mem)"
for i in 1 2 3; do
  echo "gpu load $i:"
  docker exec $name sh -c "ollama run $tag 'Say hello.' --verbose 2>&1 | grep -E 'load duration|prompt eval duration|total duration'" || true
  echo "  loaded:     $(mem)"
  docker exec $name ollama stop $tag
  sleep 3
  echo "  unloaded:   $(mem)"
done
echo "server log:"
docker logs $name 2>&1 | grep -iE "offload|starting runner|runner started|load_backend|compil|took" | head -25
docker rm -f $name >/dev/null
docker volume rm $store >/dev/null
