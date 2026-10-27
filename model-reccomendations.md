# Suggestions using AI. 
### [Conslusions from Readme of project]
`blender-open-mcp` is actually model-agnostic. It doesn't depend on any specific LLM architecture-it only expects an Ollama-compatible chat model. The README only demonstrates `llama3.2` and `gemma3`, but internally it simply sends prompts to the Ollama API and even exposes tools to list and switch installed models.

## Compatibility Table
| Model Family    | Examples                                            | Supported  | Notes                                        |
| --------------- | --------------------------------------------------- | ---------- | -------------------------------------------- |
| **Llama**       | Llama 3, 3.1, 3.2, Llama 4 (if available in Ollama) | ✅ Yes      | Primary tested family                        |
| **Qwen**        | Qwen2.5, Qwen3                                      | ✅ Yes      | Excellent reasoning, one of the best choices |
| **Gemma**       | Gemma 2, Gemma 3                                    | ✅ Yes      | README explicitly mentions Gemma3            |
| **Mistral**     | Mistral 7B, Ministral                               | ✅ Yes      | Works normally                               |
| **DeepSeek**    | DeepSeek-R1, DeepSeek-V3                            | ✅ Yes      | Strong reasoning models                      |
| **Phi**         | Phi-3, Phi-4                                        | ✅ Yes      | Lightweight option                           |
| **Command-R**   | Command R, R+                                       | ✅ Yes      | Good instruction following                   |
| **OpenChat**    | OpenChat 3.5                                        | ✅ Yes      | Compatible                                   |
| **TinyLlama**   | TinyLlama                                           | ✅ Yes      | Limited capability                           |
| **Dolphin**     | Dolphin-Llama, Dolphin-Mistral                      | ✅ Yes      | Fine-tuned models work                       |
| **Nous Hermes** | Hermes 2, Hermes 3                                  | ✅ Yes      | Good instruction tuning                      |
| **Granite**     | IBM Granite                                         | ✅ Yes      | Ollama supported                             |
| **Falcon**      | Falcon 3                                            | ✅ Yes      | Should work                                  |
| **Aya**         | Aya Expanse                                         | ✅ Yes      | Multilingual                                 |
| **EXAONE**      | EXAONE 3.x                                          | ✅ Yes      | If installed in Ollama                       |
| **StarCoder**   | StarCoder2                                          | ⚠️ Limited | Better for code than planning                |
| **CodeLlama**   | CodeLlama                                           | ⚠️ Limited | Better for Python generation                 |

## Vision Models
| Model            | Can Control Blender? | Can See Blender Screenshots? |
| ---------------- | -------------------- | ---------------------------- |
| Gemma 3 Vision   | ✅                    | ✅                            |
| Qwen2.5-VL       | ✅                    | ✅                            |
| Qwen3-VL         | ✅                    | ✅                            |
| Llama 3.2 Vision | ✅                    | ✅                            |
| MiniCPM-V        | ✅                    | ✅                            |
| InternVL         | ✅                    | ✅                            |

Vision models become useful if you extend the MCP server to send rendered images or viewport screenshots back to the model. The current repository is primarily text-based, although the Blender ecosystem has other MCP implementations that add screenshot-based workflows.

## Reasoning Models
These are probably the strongest options for Blender automation:
| Model         | Rating |
| ------------- | ------ |
| Qwen3 30B     | ⭐⭐⭐⭐⭐  |
| DeepSeek R1   | ⭐⭐⭐⭐⭐  |
| Llama 3.3 70B | ⭐⭐⭐⭐⭐  |
| Gemma 3 27B   | ⭐⭐⭐⭐☆  |
| Qwen2.5 32B   | ⭐⭐⭐⭐⭐  |
| Mistral Large | ⭐⭐⭐⭐☆  |

## Small Local Models
| RAM    | Suggested Models                       |
| ------ | -------------------------------------- |
| 8 GB   | Phi-4 Mini, Gemma 3 4B, Qwen2.5 3B     |
| 16 GB  | Gemma 3 12B, Qwen3 8B, Llama3.2 8B     |
| 24 GB  | Qwen3 14B, DeepSeek 14B                |
| 48+ GB | Qwen3 30B, Gemma 3 27B                 |
| 64+ GB | Llama 3.3 70B (quantized), DeepSeek R1 |

## Models not directly supported

These don't work out of the box because the project talks specifically to an Ollama server:
| Model/Provider     | Default Support | Possible?                                               |
| ------------------ | --------------- | ------------------------------------------------------- |
| GPT-5.5            | ❌               | Yes, by replacing the Ollama client with the OpenAI API |
| Claude Opus/Sonnet | ❌               | Yes, via Anthropic API                                  |
| Gemini 2.5         | ❌               | Yes, via Google AI Studio/Vertex API                    |
| Grok               | ❌               | Yes, via xAI API                                        |
| OpenRouter models  | ❌               | Yes, with a custom adapter                              |
| Azure OpenAI       | ❌               | Yes, with a custom adapter                              |
