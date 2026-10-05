#!/usr/bin/env python3
"""diff.py <golden-dir> <candidate-dir> [--bucket b] [--show N] [--json]
Per bucket (and findings:<type>): golden/candidate/matched/missing/extra/parity.
parity = matched / max(golden, candidate) * 100. Always exits 0."""
import argparse, json, os, sys
from collections import Counter, defaultdict


def load(d):
    out = {}
    for root, _, fs in os.walk(d):
        for f in fs:
            if f.endswith('.json'):
                p = os.path.join(root, f)
                with open(p) as fh:
                    doc = json.load(fh)
                norm_uris(doc)
                out[doc['file']] = doc
    return out


def norm_uris(doc):
    # absolute per-run uris (scratch paths) -> 'file:<rel>' so candidates can match
    for els in doc.get('analysis', {}).values():
        for e in els:
            u = e.get('uri')
            if isinstance(u, str) and u.startswith('file:') and u.endswith(doc['file']):
                e['uri'] = 'file:' + doc['file']


def ck(e):
    return json.dumps(e, sort_keys=True, separators=(',', ':'))


def pos(e):
    return (e.get('row'), e.get('col'), e.get('name') or e.get('to') or e.get('type'))


def buckets(doc):
    """yield (bucket, elements)"""
    if not doc:
        return
    for b, els in doc.get('analysis', {}).items():
        yield b, els
    by = defaultdict(list)
    for f in doc.get('findings', []):
        by['findings:' + str(f.get('type'))].append(f)
    yield from by.items()


def fdiff(g, c):
    keys = sorted(set(g) | set(c))
    return ['%s: %s -> %s' % (k, json.dumps(g.get(k, '<absent>')), json.dumps(c.get(k, '<absent>')))
            for k in keys if g.get(k, '<absent>') != c.get(k, '<absent>')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('golden'); ap.add_argument('candidate')
    ap.add_argument('--bucket'); ap.add_argument('--show', type=int, default=10)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    G, C = load(a.golden), load(a.candidate)
    stats = defaultdict(lambda: Counter())
    mism = []  # (bucket, file, row, col, lines)
    for fn in sorted(set(G) | set(C)):
        gb = dict(buckets(G.get(fn))); cb = dict(buckets(C.get(fn)))
        for b in sorted(set(gb) | set(cb)):
            if a.bucket and a.bucket != b and not b.startswith(a.bucket + ':'):
                continue
            ge, ce = gb.get(b, []), cb.get(b, [])
            gc, cc = Counter(map(ck, ge)), Counter(map(ck, ce))
            common = gc & cc
            s = stats[b]
            s['golden'] += len(ge); s['candidate'] += len(ce); s['matched'] += sum(common.values())
            gm, cm = gc - common, cc - common
            s['missing'] += sum(gm.values()); s['extra'] += sum(cm.values())
            if not gm and not cm:
                continue
            # pair leftovers by (row,col,name)
            cidx = defaultdict(list)
            for k in cm.elements():
                cidx[pos(json.loads(k))].append(json.loads(k))
            for k in gm.elements():
                g = json.loads(k); cand = cidx.get(pos(g))
                if cand:
                    mism.append((b, fn, g.get('row'), g.get('col'), ['DIFF ' + l for l in fdiff(g, cand.pop(0))]))
                else:
                    mism.append((b, fn, g.get('row'), g.get('col'), ['MISSING ' + json.dumps(g)[:160]]))
            for lst in cidx.values():
                for e in lst:
                    mism.append((b, fn, e.get('row'), e.get('col'), ['EXTRA ' + json.dumps(e)[:160]]))
    rows = {}
    for b, s in sorted(stats.items()):
        den = max(s['golden'], s['candidate'])
        rows[b] = dict(s, parity=round(100.0 * s['matched'] / den, 2) if den else 100.0)
    tot = Counter()
    for s in stats.values():
        tot.update(s)
    den = max(tot['golden'], tot['candidate'])
    total = dict(tot, parity=round(100.0 * tot['matched'] / den, 2) if den else 100.0)
    if a.json:
        print(json.dumps({'buckets': rows, 'total': total, 'files_golden': len(G), 'files_candidate': len(C),
                          'files_missing': len(set(G) - set(C)), 'files_extra': len(set(C) - set(G))}, indent=1))
        return
    print('%-34s %8s %8s %8s %8s %8s %7s' % ('bucket', 'golden', 'cand', 'matched', 'missing', 'extra', 'parity'))
    for b, r in rows.items():
        print('%-34s %8d %8d %8d %8d %8d %6.1f%%' % (b, r['golden'], r['candidate'], r['matched'], r['missing'], r['extra'], r['parity']))
    print('%-34s %8d %8d %8d %8d %8d %6.1f%%' % ('TOTAL', total['golden'], total['candidate'], total['matched'], total['missing'], total['extra'], total['parity']))
    print('files: golden %d candidate %d missing %d extra %d' % (len(G), len(C), len(set(G) - set(C)), len(set(C) - set(G))))
    mism.sort(key=lambda m: (m[1], m[2] or 0, m[3] or 0, m[0]))
    for b, fn, r, c, lines in mism[:a.show]:
        print('%s:%s:%s [%s]' % (fn, r, c, b))
        for l in lines[:8]:
            print('    ' + l)


main()
