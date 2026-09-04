"""Deterministic distribution functions used by the audit checks.

The algorithms are standard continued-fraction/series evaluations from Numerical Recipes.
They avoid platform-specific random state and keep scipy optional on the hot path.
"""

from __future__ import annotations

import math

_EPS = 3.0e-14
_FPMIN = 1.0e-300
_MAX_ITER = 500


def regularized_beta(x: float, a: float, b: float) -> float:
    if not (a > 0 and b > 0 and 0 <= x <= 1):
        raise ValueError("regularized_beta requires a,b>0 and 0<=x<=1")
    if x in (0.0, 1.0):
        return x
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, max(0.0, bt * _beta_fraction(x, a, b) / a))
    return min(1.0, max(0.0, 1.0 - bt * _beta_fraction(1.0 - x, b, a) / b))


def _beta_fraction(x: float, a: float, b: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / max(abs(d), _FPMIN) * (1 if d >= 0 else -1)
    h = d
    for m in range(1, _MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            return h
    raise ArithmeticError("beta continued fraction did not converge")


def regularized_gamma_q(a: float, x: float) -> float:
    if a <= 0 or x < 0:
        raise ValueError("regularized_gamma_q requires a>0 and x>=0")
    if x == 0:
        return 1.0
    if x < a + 1.0:
        term = summation = 1.0 / a
        ap = a
        for _ in range(_MAX_ITER):
            ap += 1.0
            term *= x / ap
            summation += term
            if abs(term) < abs(summation) * _EPS:
                p = summation * math.exp(-x + a * math.log(x) - math.lgamma(a))
                return min(1.0, max(0.0, 1.0 - p))
        raise ArithmeticError("gamma series did not converge")
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
            return min(1.0, max(0.0, q))
    raise ArithmeticError("gamma continued fraction did not converge")


def normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def normal_ppf(p: float) -> float:
    """Acklam inverse-normal approximation with one Halley refinement."""
    if not 0 < p < 1:
        if p == 0:
            return -math.inf
        if p == 1:
            return math.inf
        raise ValueError("p must be in [0,1]")
    a = (-39.6968302866538, 220.946098424521, -275.928510446969,
         138.357751867269, -30.6647980661472, 2.50662827745924)
    b = (-54.4760987982241, 161.585836858041, -155.698979859887,
         66.8013118877197, -13.2806815528857)
    c = (-0.00778489400243029, -0.322396458041136, -2.40075827716184,
         -2.54973253934373, 4.37466414146497, 2.93816398269878)
    d = (0.00778469570904146, 0.32246712907004, 2.445134137143,
         3.75440866190742)
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / (
            (((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / (
            (((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r)+1)
    error = normal_cdf(x) - p
    return x - error / (math.exp(-x*x/2) / math.sqrt(2*math.pi))


def student_t_two_sided_p(t_value: float, df: float) -> float:
    if df <= 0:
        raise ValueError("df must be positive")
    x = df / (df + t_value * t_value)
    return regularized_beta(x, df / 2.0, 0.5)


def f_sf(value: float, df1: float, df2: float) -> float:
    if value < 0 or df1 <= 0 or df2 <= 0:
        raise ValueError("F requires value>=0 and positive dfs")
    x = df2 / (df2 + df1 * value)
    return regularized_beta(x, df2 / 2.0, df1 / 2.0)


def chi2_sf(value: float, df: float) -> float:
    if value < 0 or df <= 0:
        raise ValueError("chi-square requires value>=0 and df>0")
    return regularized_gamma_q(df / 2.0, value / 2.0)

