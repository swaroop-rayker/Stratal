# Spike S-11: which LLM, and what serving it costs (M12; see s11_llm.py).
#
# Ollama runs with no network at all (--network none) from the GGUF files that
# `models pull` verified on the data volume; its own model store is a throwaway
# volume, removed at the end. The client container shares Ollama's network
# namespace, so it reaches 127.0.0.1:11434 and nothing else. A fresh Ollama
# container per model, so each memory.peak is that model's alone.
#
#   .\scripts\spikes\s11_llm.ps1            # both models, then the embedding part
#
# Run with the stack's GPU users stopped (no inference, no GPU job): one GPU job at a time.

param([string[]]$Models = @("qwen3-1.7b-q4km", "smollm3-3b-q4km"))
$ErrorActionPreference = "Stop"
$Image = "ollama/ollama:0.40.16"
$Store = "s11-ollama"
$Name = "s11-ollama"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Results = Join-Path $Root "docs\spikes\S-11-results.json"
$Memory = Join-Path $Root "docs\spikes\S-11-memory.json"
$Files = @{
    "qwen3-1.7b-q4km" = "/data/models/qwen3-1.7b-q4km/d7f544eead698dbd1f15126ef60b45a1e1933222/Qwen3-1.7B-Q4_K_M.gguf"
    "smollm3-3b-q4km" = "/data/models/smollm3-3b-q4km/4965cb60b150737b68a0408c36aeefb65078f894/SmolLM3-Q4_K_M.gguf"
}
$mem = [ordered]@{}

function Read-Cgroup([string]$file) {
    [int64](docker exec $Name sh -c "cut -d' ' -f1 /sys/fs/cgroup/$file") / 1MB
}

function Invoke-Client([string]$part, [string]$model) {
    docker run --rm --network "container:$Name" --gpus all `
        -v "${Root}\scripts:/app/scripts:ro" -v "${Root}\docs:/docs:ro" `
        -v "${Root}\docs\spikes:/out" -v "${Root}\sources\coco10k-captions:/captions:ro" `
        stratal-worker-gpu python scripts/spikes/s11_llm.py --part $part --model $model
    if ($LASTEXITCODE -ne 0) { throw "client $part $model failed" }
}

docker volume create $Store | Out-Null
try {
    foreach ($model in $Models) {
        docker rm -f $Name 2>$null | Out-Null
        docker run -d --name $Name --gpus all --network none --memory 4g -e OLLAMA_NO_CLOUD=1 `
            -v stratal_stratal-data:/data:ro -v "${Store}:/root/.ollama" $Image | Out-Null
        Start-Sleep -Seconds 3
        $tag = "s11-$model"
        docker exec $Name sh -c "printf 'FROM %s\nPARAMETER num_ctx 4096\n' '$($Files[$model])' > /tmp/Modelfile && ollama create $tag -f /tmp/Modelfile"
        if ($LASTEXITCODE -ne 0) { throw "ollama create $tag failed" }
        $idle = Read-Cgroup "memory.current"
        Invoke-Client "gpu" $tag
        $gpuPeak = Read-Cgroup "memory.peak"
        Invoke-Client "cpu" $tag
        $mem[$tag] = [ordered]@{
            idle_mib = $idle
            gpu_phase_peak_mib = $gpuPeak
            with_cpu_phase_peak_mib = Read-Cgroup "memory.peak"
            version = (docker exec $Name ollama --version) -join " "
        }
        docker rm -f $Name | Out-Null
    }
} finally {
    docker rm -f $Name 2>$null | Out-Null
    docker volume rm $Store 2>$null | Out-Null
}

# inference's CPU models (no Ollama), in inference's own memory limit
docker run --rm --memory 1250m -v stratal_stratal-data:/data:ro -v "${Root}\scripts:/app/scripts:ro" `
    -v "${Root}\docs\spikes:/out" stratal-worker-gpu python scripts/spikes/s11_llm.py --part embed
$mem | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $Memory
Write-Output "S-11 done: $Results, $Memory"
