"""Per-call and cumulative OpenAI cost tracking for the generation pipeline.

Cost is computed from the token usage returned on each API response
(`usage.input_tokens` / `output_tokens`, or the chat-completions
`prompt_tokens` / `completion_tokens`) multiplied by the model's per-token
price. Prices are in USD per 1,000,000 tokens and can be overridden at runtime
with --price-in / --price-out.

For org-wide *billed* cost reconciliation, see org_costs.py, which queries the
OpenAI Organization Costs API.
"""

import os
import json
import time
from os import path

# USD per 1,000,000 tokens: (input, output).
# These are NOT authoritative -- set the real numbers here or pass
# --price-in / --price-out on the command line.
PRICING = {
    'gpt-5.4-mini': (None, None),   # unknown to this script; supply prices
}


def normalize_usage(resp):
    """Extract (input, output, total) token counts from a Responses or
    ChatCompletions response (object or dict)."""
    usage = getattr(resp, 'usage', None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get('usage')
    if usage is None:
        return {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}

    def get(obj, *names):
        for n in names:
            v = getattr(obj, n, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(n)
            if v is not None:
                return v
        return 0

    inp = get(usage, 'input_tokens', 'prompt_tokens')
    out = get(usage, 'output_tokens', 'completion_tokens')
    tot = get(usage, 'total_tokens') or (inp + out)
    return {'input_tokens': inp, 'output_tokens': out, 'total_tokens': tot}


def resolve_price(model, price_in, price_out):
    """Fill in missing prices from the PRICING table if available."""
    table = PRICING.get(model)
    if price_in is None and table:
        price_in = table[0]
    if price_out is None and table:
        price_out = table[1]
    return price_in, price_out


def compute_cost(usage, price_in, price_out):
    if price_in is None or price_out is None:
        return None
    return (usage['input_tokens'] / 1e6) * price_in + \
           (usage['output_tokens'] / 1e6) * price_out


class CostTracker:
    """Accumulates per-call cost, persists to JSON (resumable across runs)."""

    def __init__(self, model, price_in, price_out, save_path, logger=print):
        self.model = model
        self.price_in, self.price_out = resolve_price(model, price_in, price_out)
        self.save_path = save_path
        self.log = logger
        self.calls = []
        self.totals = {'input_tokens': 0, 'output_tokens': 0,
                       'total_tokens': 0, 'cost_usd': 0.0}
        if save_path and path.exists(save_path):
            try:
                with open(save_path) as f:
                    data = json.load(f)
                self.calls = data.get('calls', [])
                self.totals = data.get('totals', self.totals)
            except (ValueError, OSError):
                pass
        if self.price_in is None or self.price_out is None:
            self.log(f'    [cost] WARNING: no pricing for {model}; pass '
                     f'--price-in/--price-out (USD per 1M tokens). '
                     f'Recording token counts only.')

    def add(self, bug_id, usage):
        cost = compute_cost(usage, self.price_in, self.price_out)
        if cost is not None:
            cost = round(cost, 6)
        self.calls.append({'bug_id': bug_id, **usage, 'cost_usd': cost,
                           'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
        self.totals['input_tokens'] += usage['input_tokens']
        self.totals['output_tokens'] += usage['output_tokens']
        self.totals['total_tokens'] += usage['total_tokens']
        if cost is not None:
            self.totals['cost_usd'] = round(self.totals['cost_usd'] + cost, 6)

        call_str = f'${cost:.4f}' if cost is not None else 'n/a'
        cum_str = (f"${self.totals['cost_usd']:.4f}"
                   if self.price_in is not None else 'n/a')
        self.log(f"    [cost] {bug_id}: in={usage['input_tokens']} "
                 f"out={usage['output_tokens']} call={call_str} "
                 f"cumulative={cum_str} ({len(self.calls)} calls)")
        self.save()
        return cost

    def save(self):
        if not self.save_path:
            return
        os.makedirs(path.dirname(self.save_path) or '.', exist_ok=True)
        with open(self.save_path, 'w') as f:
            json.dump({
                'model': self.model,
                'price_per_1m_usd': {'input': self.price_in, 'output': self.price_out},
                'totals': self.totals,
                'calls': self.calls,
            }, f, indent=2)
