# Muse Glimmer 30B 4-bit provider

CIOAgent supports Muse Glimmer as `service: muse` for every committee role,
specialist/subagent, WMA, translator, and general bot chat. The provider talks
to an OpenAI-compatible local endpoint, so the model weights stay outside the
CIOAgent process and either supported 4-bit representation can be used:

- `UD-Q4_K_XL`: Unsloth Dynamic GGUF served by `llama-server`.
- `NVFP4`: an NVFP4 deployment exposed through an OpenAI-compatible server
  such as vLLM. This path requires compatible NVIDIA hardware and current
  inference packages.

Meta describes Muse Glimmer as a 29.6B agentic, tool-using, multimodal model
with 131,072+ context. Unsloth lists both 4-bit options at approximately 17 GB+
and recommends temperature 1.0, top-p 0.95, and top-k 64. CIOAgent sends those
sampling values for one-shot agent/subagent calls and adds the configured
reasoning strength (`high` by default) to the system prompt.

## UD-Q4_K_XL with llama-server

This is the verified path for the local RTX 4090 (24 GB VRAM). Use a current
Muse-compatible llama.cpp checkout; older binaries may not recognize the model
architecture.

### Build llama-server with CUDA

The local checkout is `/mnt/AIWorkSpace/work/llama.cpp`. Configure it for the
RTX 4090's CUDA compute capability 8.9 and build with bounded parallelism:

```bash
cd /mnt/AIWorkSpace/work/llama.cpp

cmake -B build \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DGGML_CUDA_FA_ALL_QUANTS=OFF \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build \
  --target llama-server \
  --parallel 4
```

`GGML_CUDA_FA_ALL_QUANTS=OFF` is deliberate. The normal CUDA build already
includes the matching FlashAttention KV-cache combinations used here:
`q8_0/q8_0` and `q4_0/q4_0`. Enabling all combinations adds many CUDA template
translation units and is unnecessary for this deployment.

The resulting executable is not placed at the repository root. Verify the
actual CMake output:

```bash
./build/bin/llama-server --version
./build/bin/llama-server --list-devices
```

`--list-devices` should include the RTX 4090.

### Resolved build failure: host out of memory

The initial build used both `GGML_CUDA_FA_ALL_QUANTS=ON` and `-j` without a job
count. With the Unix Makefiles generator, bare `-j` allows effectively
unbounded parallel work. The all-quant option simultaneously asks nvcc to
compile every supported FlashAttention KV-cache combination. On the local
32-GB RAM, 32-thread machine this launched many `nvcc`/`cudafe++` processes and
exhausted host RAM and swap.

The kernel log established the root cause; this was not a CUDA compatibility
or llama.cpp source error:

```text
Out of memory: Killed process ... (cudafe++)
```

The following configuration messages were not failures:

- `LLAMA_CURL is deprecated and will be ignored`: harmless; the current build
  no longer needs that legacy option.
- `NCCL not found`: harmless for this single-GPU RTX 4090 deployment.
- `Using CMAKE_CUDA_ARCHITECTURES=89-real`: correct for the RTX 4090.

If all mixed KV-cache combinations are needed in the future, configure
`GGML_CUDA_FA_ALL_QUANTS=ON` but build with `--parallel 2` or
`--parallel 4`, never bare `-j` on this machine.

### Download the model and vision projector

Following Unsloth's deployment recipe, download the GGUF and matching vision
projector:

```bash
hf download unsloth/Muse-Glimmer-30B-GGUF \
  --local-dir models/Muse-Glimmer-30B-GGUF \
  --include '*UD-Q4_K_XL*' \
  --include '*mmproj*'
```

`mmproj-kquant.gguf` is Muse Glimmer's quantized perception encoder/projector.
It converts images into features the language model can consume. Its
quantization is independent of the main model's `UD-Q4_K_XL` name, so a file
named `mmproj-Q4_K_XL.gguf` is neither expected nor required. The server can
run text and tool calls without `--mmproj`; image understanding requires it.

### Run the server

From `/mnt/AIWorkSpace/work/llama.cpp`, run the executable from its real build
location:

```bash
./build/bin/llama-server \
  --model models/Muse-Glimmer-30B-GGUF/Muse-Glimmer-30B-UD-Q4_K_XL.gguf \
  --mmproj models/Muse-Glimmer-30B-GGUF/mmproj-kquant.gguf \
  --alias muse-glimmer-30b-ud-q4-k-xl \
  --ctx-size 32768 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --flash-attn on \
  --fit on \
  --jinja \
  --temp 1.0 \
  --top-p 0.95 \
  --top-k 64 \
  --host 127.0.0.1 \
  --port 8001
```

The model weights remain 4-bit `UD-Q4_K_XL`. The `q8_0` settings above apply
only to the runtime attention KV cache; they do not change the model to Q8.
Q8 KV cache is the preferred quality/memory compromise. If model startup runs
out of VRAM, reduce context first or use `q4_0` for both cache settings:

```bash
--cache-type-k q4_0 --cache-type-v q4_0
```

Do not mix cache types unless llama.cpp was built with the corresponding
all-quant specialization.

Confirm that the OpenAI-compatible API is available:

```bash
curl http://127.0.0.1:8001/v1/models
```

The response should contain `muse-glimmer-30b-ud-q4-k-xl`. If the downloaded
projector has a different current filename, pass that file to `--mmproj`; the
Hugging Face repository is authoritative for artifact names.

## NVFP4

Unsloth's current Muse guide calls for Python 3.13 and these minimum packages:

```bash
uv venv unsloth-nvfp4-env --python 3.13
source unsloth-nvfp4-env/bin/activate
uv pip install 'vllm>=0.25.0' 'flashinfer-python>=0.6.13' \
  'nvidia-cutlass-dsl>=4.5.2' --torch-backend=auto
```

Serve the selected Muse Glimmer NVFP4 artifact on port 8001 with the served
model name `muse-glimmer-30b-nvfp4`. Keep the provider model name and server
alias identical; CIOAgent does not assume a particular third-party NVFP4
repository.

## CIOAgent configuration

The checked-in configuration uses:

```yaml
muse:
  base_url: http://127.0.0.1:8001/v1
  api_key_env: CIO_MUSE_API_KEY
  max_tokens: 20480
  timeout: 300
  reasoning_strength: xhigh
```

No API key is required for a normal localhost deployment. Set
`CIO_MUSE_API_KEY` only when the server enforces bearer authentication.
Environment overrides are `CIO_MUSE_BASE_URL`, `CIO_MUSE_MAX_TOKENS`,
`CIO_MUSE_TIMEOUT`, and `CIO_MUSE_REASONING_STRENGTH`.

Muse supports `low`, `medium`, `high`, and `xhigh` reasoning effort. The
default is `xhigh`. Set it per fallback-chain link when different agents need
different effort levels:

```yaml
- {service: muse, model: muse-glimmer-30b-ud-q4-k-xl, reasoning_effort: high}
```

`CIO_MUSE_REASONING_STRENGTH` overrides every Muse link, which is useful for a
temporary process-wide setting. The effective effort is added to Muse's system
prompt as `Reasoning strength: <level>.`, as specified by the model guide.

Every built-in fallback chain now contains a Muse link. The default is the
UD-Q4 alias. To use NVFP4, change a chain link in `/configure` or
`config/committee_models.yaml`:

```yaml
- {service: muse, model: muse-glimmer-30b-nvfp4}
```

Provider and model changes are picked up on the next committee call; bot chat
rebuilds its cached runtime on the next message.

Sources: [Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B),
[Unsloth Muse Glimmer guide](https://unsloth.ai/docs/models/muse-glimmer), and
[llama.cpp CUDA build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#cuda).
