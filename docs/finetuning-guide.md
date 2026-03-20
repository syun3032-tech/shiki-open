# Gemini Function Calling ファインチューニング ガイド

> 調査日: 2026-03-13
> ステータス: 保留（まず現行改善の精度を確認してから）

## 結論

- Gemini 2.5のFunction Calling用SFTは2026年3月現在バグで動かない
- **FunctionGemma（270Mパラメータ）がローカルツールルーターとして最有力**
- 精度58%→85%の実績、ローカルMacで学習・推論可能、コスト0円

## FunctionGemma とは

- Google純正の Function Calling特化モデル
- Gemma 3ベース、たった270Mパラメータ（超軽量）
- Unsloth / HuggingFace / Ollama で動く
- ローカルMacで学習も推論も完結

## 識ちゃんへの適用

```
ユーザー入力 → FunctionGemma（ローカル、ツール選択特化）
                 ↓ どのツールを使うか決定
             Gemini 2.5 Pro（会話生成、コンテキスト理解）
                 ↓
             ツール実行 → 結果報告
```

## やるときの手順

### 1. 学習データ作成（500件目標）

識ちゃんの操作ログから自動生成 + 手動チェック。

JSONL形式:
```json
{
  "system_instruction": {
    "role": "system",
    "parts": [{"text": "あなたは識ちゃんです。オーナーのPC操作を手伝うAIアシスタント。"}]
  },
  "contents": [
    {
      "role": "user",
      "parts": [{"text": "デスクトップのスクショ撮って"}]
    },
    {
      "role": "model",
      "parts": [{"functionCall": {"name": "take_screenshot", "args": {}}}]
    },
    {
      "role": "user",
      "parts": [{"functionResponse": {"name": "take_screenshot", "response": {"result": "screenshot.png"}}}]
    },
    {
      "role": "model",
      "parts": [{"text": "撮ったよ。"}]
    }
  ],
  "tools": [
    {
      "functionDeclarations": [
        {
          "name": "take_screenshot",
          "description": "PC画面のスクリーンショットを撮影する",
          "parameters": {"type": "OBJECT", "properties": {}}
        }
      ]
    }
  ]
}
```

### 2. FunctionGemmaファインチューニング

```bash
# Unslothインストール
pip install unsloth

# HuggingFaceからモデル取得
# google/functiongemma-270m-it

# ファインチューニング実行（ローカルMacで数時間）
# 詳細: docs.unsloth.ai/models/functiongemma
```

### 3. Ollama でローカルサーブ

```bash
# ファインチューニング後のモデルをOllama形式に変換してサーブ
ollama create shiki-router -f Modelfile
ollama run shiki-router
```

### 4. 識ちゃんに組み込み

agent/loop.py の `_generate_plan()` の代わりに FunctionGemma を使う。

## スペック

| 項目 | 詳細 |
|------|------|
| 最低データ数 | 100件 |
| 推奨 | 500〜1000件 |
| 学習コスト | ローカルなら0円 |
| 推論コスト | ローカルなら0円 |
| 学習時間 | 数百件で20分〜1時間 |
| 精度向上 | 58%→85%（公式実績） |

## 代替手段

| 方法 | 確実性 | コスト | 備考 |
|------|--------|--------|------|
| **FunctionGemma** | ◎ | 無料 | ローカル、推奨 |
| OpenAI gpt-4o-mini SFT | ◎ | 安い | 確実に動く |
| Vertex AI Gemini SFT | △ | 安い | FC用は現在バグ |
| Mistral SFT | ○ | 安い | OSS |

## 参考リンク

- [FunctionGemma (HuggingFace)](https://huggingface.co/google/functiongemma-270m-it)
- [FunctionGemma Guide (Google)](https://developers.googleblog.com/a-guide-to-fine-tuning-functiongemma/)
- [Unsloth FunctionGemma](https://docs.unsloth.ai/models/functiongemma)
- [Vertex AI SFT Docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/tune-function-calling)
- [OpenAI FC Fine-tuning](https://cookbook.openai.com/examples/fine_tuning_for_function_calling)
- [FC SFTバグ報告](https://discuss.google.dev/t/270649)
