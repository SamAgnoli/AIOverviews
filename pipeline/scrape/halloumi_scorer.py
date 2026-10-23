"""
Python wrapper around the HallOumi-8B-classifier HuggingFace model for
hallucination / claim-support scoring.

Mirrors the prompt format used by Oumi's halloumi-demo TypeScript client
(see halloumi/preprocessing.ts -> createHalloumiClassifierPrompt):

    <context>
    {context}
    </context>

    <claims>
    {claim}
    </claims>

The classifier is binary (supported vs unsupported) and is meant to be
called once per claim, so we split the response into sentences and average
the per-claim supported probability into a single overall score.

Usage:
    from halloumi_scorer import HalloumiScorer
    scorer = HalloumiScorer()         # loads model (slow first call)
    scorer.score(context_text, response_text)
    # -> {"halloumi_supported_prob": float,
    #     "halloumi_unsupported_prob": float,
    #     "halloumi_n_claims": int}
"""

import re


class HalloumiScorer:
    def __init__(
        self,
        model_id="oumi-ai/HallOumi-8B-classifier",
        device="cuda",
        load_in_4bit=True,
        max_model_tokens=8192,
    ):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        self.model_id = model_id
        self.max_model_tokens = max_model_tokens

        # T4 (Turing) has no native bfloat16 — use float16 for both the load
        # dtype and the 4-bit compute dtype so an 8B model fits in ~5.5 GB on a
        # 15 GB T4. low_cpu_mem_usage avoids a transient full-precision copy.
        load_kwargs = {
            "dtype": torch.float16,
            "low_cpu_mem_usage": True,
        }
        if load_in_4bit:
            from transformers import BitsAndBytesConfig
            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
            load_kwargs["device_map"] = "auto"
        else:
            load_kwargs["device_map"] = device

        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        try:
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_id, **load_kwargs
            )
        except TypeError:
            # Older transformers used `torch_dtype` instead of `dtype`.
            load_kwargs["torch_dtype"] = load_kwargs.pop("dtype")
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_id, **load_kwargs
            )
        self.model.eval()

        # Sanity flag: confirm 4-bit actually engaged. If this is False on a T4,
        # the model loaded full-precision and an OOM is imminent — surface it.
        self.is_4bit = bool(getattr(self.model, "is_loaded_in_4bit", False))
        if load_in_4bit and not self.is_4bit:
            print("⚠️  WARNING: model did NOT load in 4-bit — check bitsandbytes / "
                  "transformers versions (it will likely OOM on a T4).")

        # Resolve which logit index corresponds to "supported".
        id2label = getattr(self.model.config, "id2label", None) or {}
        supported_idx = None
        for idx, label in id2label.items():
            ls = str(label).lower()
            if "support" in ls and "unsupport" not in ls and "un_support" not in ls:
                supported_idx = int(idx)
                break
        # Fallback: HF convention is label 1 = positive class.
        self.supported_idx = 1 if supported_idx is None else supported_idx

    @staticmethod
    def _split_into_claims(text):
        text = (text or "").strip()
        if not text:
            return []
        # Lightweight sentence splitter — good enough for AIO answers,
        # which are short and well-punctuated.
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p.strip() for p in parts if p.strip()]

    def _truncate_to_tokens(self, text, max_tokens):
        ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) <= max_tokens:
            return text
        return self.tokenizer.decode(ids[:max_tokens], skip_special_tokens=True)

    @staticmethod
    def _build_prompt(context, claim):
        return (
            f"<context>\n{context.strip()}\n</context>\n\n"
            f"<claims>\n{claim.strip()}\n</claims>"
        )

    def _forward_supported_prob(self, ctx, claim):
        """One forward pass -> P(supported). Raises on OOM (caller handles)."""
        import torch
        prompt = self._build_prompt(ctx, claim)
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_model_tokens,
        ).to(self.model.device)
        with torch.inference_mode():
            logits = self.model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0]
        return float(probs[self.supported_idx].item())

    def _chunk_by_tokens(self, text, chunk_tokens, overlap_tokens):
        """Split text into token windows with overlap — every token appears in
        at least one chunk, so nothing is lost to truncation."""
        ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) <= chunk_tokens:
            return [text]
        chunks, start = [], 0
        step = max(1, chunk_tokens - overlap_tokens)
        while start < len(ids):
            window = ids[start:start + chunk_tokens]
            chunks.append(self.tokenizer.decode(window, skip_special_tokens=True))
            if start + chunk_tokens >= len(ids):
                break
            start += step
        return chunks

    def score_max(self, context, claim, chunk_tokens=2800, overlap_tokens=200,
                  max_context_chars=200000, stop_at=0.995):
        """
        Score ONE claim against a source's FULL text: chunk the source into
        token windows and take the MAX supported-prob across chunks. Unlike
        score(), no part of the source is thrown away — a claim supported
        anywhere in the document gets credit, and GPU memory stays bounded by
        chunk_tokens regardless of document length.
        stop_at: stop reading further chunks once one reaches this prob. Pass
        the support threshold to skip the rest of a page that already supports
        the claim -- the supported/unsupported call is unchanged, but the
        returned prob is then the first chunk >= stop_at, not the page max.
        Returns {"supported_prob": float|None, "n_chunks": int}.
        """
        import torch

        if not isinstance(context, str) or not context.strip() or \
           not isinstance(claim, str) or not claim.strip():
            return {"supported_prob": None, "n_chunks": 0}
        if len(context) > max_context_chars:
            context = context[:max_context_chars]

        chunks = self._chunk_by_tokens(context, chunk_tokens, overlap_tokens)
        best = None
        for ch in chunks:
            try:
                p = self._forward_supported_prob(ch, claim)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                try:
                    half = self._truncate_to_tokens(ch, chunk_tokens // 2)
                    p = self._forward_supported_prob(half, claim)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    continue          # skip this chunk, keep scanning
            if best is None or p > best:
                best = p
            if best >= stop_at:
                break                 # page already supports the claim — stop scanning
        return {"supported_prob": best, "n_chunks": len(chunks)}

    def score(self, context, claims, max_context_tokens=3000,
              max_context_chars=40000):
        """
        max_context_tokens=3000 keeps the attention workspace for the 8B model
        small enough that the full run fits in a T4's 15 GB alongside the 4-bit
        weights (~4.7 GB). 6000-token contexts OOMed partway through real runs.
        On a per-claim OOM we halve the context and retry once, then skip the
        claim — a long claim never kills the whole scoring run.
        """
        import torch

        empty = {
            "halloumi_supported_prob": None,
            "halloumi_unsupported_prob": None,
            "halloumi_n_claims": 0,
        }
        if not isinstance(context, str) or not context.strip():
            return empty
        if not isinstance(claims, str) or not claims.strip():
            return empty

        claim_list = self._split_into_claims(claims)
        if not claim_list:
            return empty

        # Pre-cap raw chars BEFORE tokenizing — the fallback path concatenates
        # every source for a question (can be 100KB+); tokenizing all of it just
        # to throw most away wastes CPU/RAM.
        if len(context) > max_context_chars:
            context = context[:max_context_chars]
        ctx = self._truncate_to_tokens(context, max_context_tokens)

        supported_scores = []
        for claim in claim_list:
            try:
                supported_scores.append(self._forward_supported_prob(ctx, claim))
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                try:
                    half_ctx = self._truncate_to_tokens(ctx, max_context_tokens // 2)
                    supported_scores.append(
                        self._forward_supported_prob(half_ctx, claim))
                    print("  ⚠ OOM — retried claim at half context, OK")
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    print("  ⚠ OOM twice — skipping one claim")
                    continue

        if not supported_scores:
            return empty
        mean_supported = sum(supported_scores) / len(supported_scores)
        return {
            "halloumi_supported_prob": mean_supported,
            "halloumi_unsupported_prob": 1.0 - mean_supported,
            "halloumi_n_claims": len(supported_scores),
        }
