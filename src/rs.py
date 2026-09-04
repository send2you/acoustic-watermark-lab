"""Reed-Solomon over GF(256), systematic. Reference implementation.

Kept dependency-free and deliberately simple so the JavaScript port in the
PWA can mirror it line for line.
"""

GF_EXP = [0] * 512
GF_LOG = [0] * 256

def _init_tables(primitive=0x11d):
    x = 1
    for i in range(255):
        GF_EXP[i] = x
        GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= primitive
    for i in range(255, 512):
        GF_EXP[i] = GF_EXP[i - 255]

_init_tables()


def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def gf_div(a, b):
    if b == 0:
        raise ZeroDivisionError
    if a == 0:
        return 0
    return GF_EXP[(GF_LOG[a] - GF_LOG[b]) % 255]


def gf_pow(a, n):
    return GF_EXP[(GF_LOG[a] * n) % 255]


def gf_inv(a):
    return GF_EXP[(255 - GF_LOG[a]) % 255]


def poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, pi in enumerate(p):
        if pi == 0:
            continue
        for j, qj in enumerate(q):
            if qj:
                r[i + j] ^= gf_mul(pi, qj)
    return r


def poly_eval(p, x):
    y = 0
    for c in p:
        y = gf_mul(y, x) ^ c
    return y


def generator_poly(nsym):
    g = [1]
    for i in range(nsym):
        g = poly_mul(g, [1, gf_pow(2, i)])
    return g


def rs_encode(data, nsym):
    """Append nsym parity bytes to data."""
    gen = generator_poly(nsym)
    out = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = out[i]
        if coef == 0:
            continue
        for j in range(1, len(gen)):
            out[i + j] ^= gf_mul(gen[j], coef)
    return bytes(data) + bytes(out[len(data):])


def _syndromes(msg, nsym):
    return [poly_eval(msg, gf_pow(2, i)) for i in range(nsym)]


def _berlekamp_massey(synd, nsym):
    err_loc = [1]
    old_loc = [1]
    for i in range(nsym):
        old_loc = old_loc + [0]
        delta = synd[i]
        for j in range(1, len(err_loc)):
            delta ^= gf_mul(err_loc[len(err_loc) - 1 - j], synd[i - j])
        if delta != 0:
            if len(old_loc) > len(err_loc):
                new_loc = [gf_mul(c, delta) for c in old_loc]
                old_loc = [gf_mul(c, gf_inv(delta)) for c in err_loc]
                err_loc = new_loc
            scale = [gf_mul(c, delta) for c in old_loc]
            err_loc = [
                (err_loc[len(err_loc) - 1 - k] if k < len(err_loc) else 0)
                ^ (scale[len(scale) - 1 - k] if k < len(scale) else 0)
                for k in range(max(len(err_loc), len(scale)))
            ][::-1]
    while err_loc and err_loc[0] == 0:
        err_loc.pop(0)
    return err_loc


def _find_errors(err_loc, nmess):
    errs = len(err_loc) - 1
    pos = []
    for i in range(nmess):
        if poly_eval(err_loc, gf_pow(2, 255 - i)) == 0:
            pos.append(nmess - 1 - i)
    if len(pos) != errs:
        raise ValueError("RS: could not locate errors")
    return pos


def _correct(msg, synd, pos):
    coef_pos = [len(msg) - 1 - p for p in pos]

    # Error locator from the found positions.
    e_loc = [1]
    for i in coef_pos:
        e_loc = poly_mul(e_loc, [gf_pow(2, i), 1])

    # Error evaluator.
    rsynd = synd[::-1]
    ee = poly_mul(rsynd, e_loc)
    ee = ee[len(ee) - len(coef_pos):]

    # Formal derivative of the locator.
    e_loc_prime = e_loc[len(e_loc) % 2::2]

    out = list(msg)
    for i, p in enumerate(coef_pos):
        xi = gf_pow(2, p)
        xi_inv = gf_inv(xi)
        num = poly_eval(ee, xi_inv)
        den = poly_eval(e_loc_prime, gf_mul(xi_inv, xi_inv))
        if den == 0:
            raise ValueError("RS: undefined error magnitude")
        mag = gf_mul(xi, gf_div(num, den))
        out[pos[i]] ^= mag
    return bytes(out)


def rs_decode(msg, nsym):
    """Correct up to nsym//2 byte errors. Returns the data portion."""
    msg = list(msg)
    synd = _syndromes(msg, nsym)
    if max(synd) == 0:
        return bytes(msg[:-nsym])
    err_loc = _berlekamp_massey(synd, nsym)
    if len(err_loc) - 1 > nsym // 2:
        raise ValueError("RS: too many errors to correct")
    pos = _find_errors(err_loc, len(msg))
    fixed = _correct(msg, synd, pos)
    if max(_syndromes(list(fixed), nsym)) != 0:
        raise ValueError("RS: correction failed")
    return bytes(fixed[:-nsym])
