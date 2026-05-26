"""Per-call and cumulative OpenAI cost tracking for the generation pipeline.

Cost is computed from the token usage returned on each API response
(`usage.input_tokens` / `output_tokens`, plus cached input tokens from
`usage.input_tokens_details.cached_tokens`; the chat-completions equivalents
are `prompt_tokens` / `completion_tokens` / `prompt_tokens_details`) multiplied
by the model's per-token price. Prices are USD per 1,000,000 tokens and can be
overridden at runtime with --price-in / --price-out / --price-cached.

For org-wide *billed* cost reconciliation, see org_costs.py, which queries the
OpenAI Organization Costs API.
"""

import os
import json
import time
from os import path

# USD per 1,000,000 tokens: (input, output, cached_input).
# cached_input is optional; if omitted, cached tokens are billed at the input
# rate. Override at runtime with --price-in / --price-out / --price-cached.
PRICING = {
    'gpt-5.5':       (5.00, 30.00, 0.50),
    'gpt-5.4':       (2.50, 15.00, 0.25),
    'gpt-5.4-mini':  (0.75,  4.50, 0.075),
    'gpt-5.2':       (1.75, 14.00, 0.175),
    'gpt-5.1':       (1.25, 10.00, 0.125),
    'gpt-5':         (1.25, 10.00, 0.125),
    'gpt-5-mini':    (0.25,  2.00, 0.025),
}


def _get(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(n)
        if v is not None:
            return v
    return None


def normalize_usage(resp):
    """Extract token counts from a Responses or ChatCompletions response.

    Returns input_tokens (total prompt tokens, including cached),
    cached_input_tokens, output_tokens, total_tokens."""
    usage = getattr(resp, 'usage', None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get('usage')
    if usage is None:
        return {'input_tokens': 0, 'cached_input_tokens': 0,
                'output_tokens': 0, 'total_tokens': 0}

    inp = _get(usage, 'input_tokens', 'prompt_tokens') or 0
    out = _get(usage, 'output_tokens', 'completion_tokens') or 0
    tot = _get(usage, 'total_tokens') or (inp + out)

    cached = 0
    details = _get(usage, 'input_tokens_details', 'prompt_tokens_details')
    if details is not None:
        cached = _get(details, 'cached_tokens') or 0

    return {'input_tokens': inp, 'cached_input_tokens': cached,
            'output_tokens': out, 'total_tokens': tot}


def resolve_price(model, price_in, price_out, price_cached):
    """Fill in missing prices from the PRICING table if available."""
    table = PRICING.get(model)
    if table:
        if price_in is None:
            price_in = table[0]
        if price_out is None:
            price_out = table[1]
        if price_cached is None and len(table) > 2:
            price_cached = table[2]
    return price_in, price_out, price_cached


def compute_cost(usage, price_in, price_out, price_cached=None):
    if price_in is None or price_out is None:
        return None
    cached = usage.get('cached_input_tokens', 0) or 0
    uncached = max(usage['input_tokens'] - cached, 0)
    cached_rate = price_in if price_cached is None else price_cached
    return (uncached / 1e6) * price_in + \
           (cached / 1e6) * cached_rate + \
           (usage['output_tokens'] / 1e6) * price_out


class CostTracker:
    """Accumulates per-call cost, persists to JSON (resumable across runs)."""

    def __init__(self, model, price_in, price_out, save_path,
                 price_cached=None, logger=print):
        self.model = model
        self.price_in, self.price_out, self.price_cached = resolve_price(
            model, price_in, price_out, price_cached)
        self.save_path = save_path
        self.log = logger
        self.calls = []
        self.totals = {'input_tokens': 0, 'cached_input_tokens': 0,
                       'output_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}
        if save_path and path.exists(save_path):
            try:
                with open(save_path) as f:
                    data = json.load(f)
                self.calls = data.get('calls', [])
                self.totals = {**self.totals, **data.get('totals', {})}
            except (ValueError, OSError):
                pass
        if self.price_in is None or self.price_out is None:
            self.log(f'    [cost] WARNING: no pricing for {model}; pass '
                     f'--price-in/--price-out (USD per 1M tokens). '
                     f'Recording token counts only.')

    def add(self, bug_id, usage):
        cost = compute_cost(usage, self.price_in, self.price_out, self.price_cached)
        if cost is not None:
            cost = round(cost, 6)
        self.calls.append({'bug_id': bug_id, **usage, 'cost_usd': cost,
                           'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
        for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens'):
            self.totals[k] += usage.get(k, 0)
        if cost is not None:
            self.totals['cost_usd'] = round(self.totals['cost_usd'] + cost, 6)

        call_str = f'${cost:.4f}' if cost is not None else 'n/a'
        cum_str = (f"${self.totals['cost_usd']:.4f}"
                   if self.price_in is not None else 'n/a')
        cached = usage.get('cached_input_tokens', 0)
        self.log(f"    [cost] {bug_id}: in={usage['input_tokens']} "
                 f"(cached={cached}) out={usage['output_tokens']} "
                 f"call={call_str} cumulative={cum_str} ({len(self.calls)} calls)")
        self.save()
        return cost

    def save(self):
        if not self.save_path:
            return
        os.makedirs(path.dirname(self.save_path) or '.', exist_ok=True)
        with open(self.save_path, 'w') as f:
            json.dump({
                'model': self.model,
                'price_per_1m_usd': {'input': self.price_in,
                                     'output': self.price_out,
                                     'cached_input': self.price_cached},
                'totals': self.totals,
                'calls': self.calls,
            }, f, indent=2)
