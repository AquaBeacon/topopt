"""Method of Moving Asymptotes (Svanberg 1987; the 2007 primal-dual form).

Why this exists. The optimality-criteria update in `generative._oc` handles
exactly one constraint -- the volume -- because it bisects one Lagrange
multiplier. A stress constraint is a second constraint (one per load case, in
fact), and OC has no place to put it. MMA is the standard optimiser for
topology problems with a handful of constraints: it builds a separable convex
approximation of the objective and every constraint around the current point,
solves that subproblem exactly, and moves on. With m constraints and n design
variables the subproblem costs O(m n) plus one m x m solve per Newton step, so
a 100,000-cell grid with seven constraints is still a fraction of a second.

What this is. A direct port of Krister Svanberg's `mmasub.m` / `subsolv.m`
(2007 version), with the same defaults, written against numpy. Nothing has
been "improved": the point of a reference algorithm is that its behaviour is
documented elsewhere, so a run that misbehaves can be compared to the
literature rather than to the author's recollection of it.

Conventions, all Svanberg's:

    minimise    f0(x) + a0 z + sum_i (c_i y_i + 0.5 d_i y_i^2)
    subject to  f_i(x) - a_i z - y_i <= 0        i = 1..m
                xmin <= x <= xmax,  y >= 0,  z >= 0

With `a = 0`, `c` large (1000) and `d = 0` -- the settings `MMA.__init__`
uses unless told otherwise -- the y_i are elastic slack on each constraint:
the subproblem is always feasible, an infeasible start is allowed, and the
optimiser pays 1000 per unit of violation, which is what makes it go to
zero. `z` is unused (a0 = 1, a = 0 fixes z = 0).

`MMA.step` takes the current point plus the objective/constraint values and
gradients and returns the next point; the object keeps the two previous
points and the asymptotes between calls, which is the "moving" in the name.
"""

import numpy as np


class MMA:
    def __init__(self, n, m, xmin, xmax, a0=1.0, a=None, c=None, d=None,
                 move=0.5, asyinit=0.5, asyincr=1.2, asydecr=0.7,
                 raa0=1e-5, albefa=0.1, epsimin=1e-7, asy_min=0.01,
                 asy_max=10.0):
        self.n, self.m = int(n), int(m)
        self.xmin = np.broadcast_to(np.asarray(xmin, float), (self.n,)).copy()
        self.xmax = np.broadcast_to(np.asarray(xmax, float), (self.n,)).copy()
        self.a0 = float(a0)
        self.a = np.zeros(self.m) if a is None else np.asarray(a, float)
        self.c = (np.full(self.m, 1000.0) if c is None
                  else np.broadcast_to(np.asarray(c, float), (self.m,)).copy())
        self.d = (np.zeros(self.m) if d is None
                  else np.broadcast_to(np.asarray(d, float), (self.m,)).copy())
        self.move = float(move)
        self.asyinit, self.asyincr, self.asydecr = asyinit, asyincr, asydecr
        self.raa0, self.albefa, self.epsimin = raa0, albefa, epsimin
        self.asy_min, self.asy_max = asy_min, asy_max
        self.iter = 0
        self.xold1 = None
        self.xold2 = None
        self.low = None
        self.upp = None
        self.last = {}

    # ------------------------------------------------------------ mmasub

    def step(self, xval, f0val, df0dx, fval, dfdx, move=None, xmin=None,
             xmax=None):
        """One MMA iteration. -> xnew (n,).

        xval   (n,)   current design
        f0val  float  objective value
        df0dx  (n,)   objective gradient
        fval   (m,)   constraint values, feasible when <= 0
        dfdx   (m, n) constraint gradients
        move   move limit as a fraction of (xmax - xmin) for this step; the
               constructor's value when None
        xmin / xmax: per-step bounds overriding the constructor's (e.g. a
               box that tightens as continuation sharpens the projection)
        """
        n, m = self.n, self.m
        xval = np.asarray(xval, float).reshape(n)
        df0dx = np.asarray(df0dx, float).reshape(n)
        fval = np.asarray(fval, float).reshape(m)
        dfdx = np.asarray(dfdx, float).reshape(m, n)
        xmin = self.xmin if xmin is None else np.broadcast_to(
            np.asarray(xmin, float), (n,))
        xmax = self.xmax if xmax is None else np.broadcast_to(
            np.asarray(xmax, float), (n,))
        move = self.move if move is None else float(move)
        self.iter += 1

        xrange = xmax - xmin
        if self.iter <= 2 or self.xold2 is None:
            low = xval - self.asyinit * xrange
            upp = xval + self.asyinit * xrange
        else:
            zzz = (xval - self.xold1) * (self.xold1 - self.xold2)
            factor = np.ones(n)
            factor[zzz > 0] = self.asyincr
            factor[zzz < 0] = self.asydecr
            low = xval - factor * (self.xold1 - self.low)
            upp = xval + factor * (self.upp - self.xold1)
            lowmin = xval - self.asy_max * xrange
            lowmax = xval - self.asy_min * xrange
            uppmin = xval + self.asy_min * xrange
            uppmax = xval + self.asy_max * xrange
            low = np.minimum(np.maximum(low, lowmin), lowmax)
            upp = np.maximum(np.minimum(upp, uppmax), uppmin)

        alfa = np.maximum(np.maximum(low + self.albefa * (xval - low),
                                     xval - move * xrange), xmin)
        beta = np.minimum(np.minimum(upp - self.albefa * (upp - xval),
                                     xval + move * xrange), xmax)
        # A variable whose bounds have met has nowhere to go; keep the
        # subproblem well-posed by leaving it a sliver.
        tight = beta - alfa < 1e-12
        if tight.any():
            mid = 0.5 * (alfa + beta)
            alfa = np.where(tight, mid - 5e-13, alfa)
            beta = np.where(tight, mid + 5e-13, beta)

        xmami = np.maximum(xrange, 1e-5)
        xmamiinv = 1.0 / xmami
        ux1 = upp - xval
        ux2 = ux1 * ux1
        xl1 = xval - low
        xl2 = xl1 * xl1

        p0 = np.maximum(df0dx, 0.0)
        q0 = np.maximum(-df0dx, 0.0)
        pq0 = 0.001 * (p0 + q0) + self.raa0 * xmamiinv
        p0 = (p0 + pq0) * ux2
        q0 = (q0 + pq0) * xl2

        P = np.maximum(dfdx, 0.0)
        Q = np.maximum(-dfdx, 0.0)
        PQ = 0.001 * (P + Q) + self.raa0 * xmamiinv[None, :]
        P = (P + PQ) * ux2[None, :]
        Q = (Q + PQ) * xl2[None, :]
        b = P @ (1.0 / ux1) + Q @ (1.0 / xl1) - fval

        x, y, z, lam, xsi, eta, mu, zet, s, iters = _subsolv(
            m, n, self.epsimin, low, upp, alfa, beta, p0, q0, P, Q,
            self.a0, self.a, b, self.c, self.d)

        self.xold2 = self.xold1
        self.xold1 = xval.copy()
        self.low, self.upp = low, upp
        self.last = {"lambda": lam.copy(), "y": y.copy(), "z": float(z),
                     "subsolve_iterations": int(iters)}
        return x


# ------------------------------------------------------------- subsolv

def _subsolv(m, n, epsimin, low, upp, alfa, beta, p0, q0, P, Q, a0, a, b, c, d):
    """Primal-dual interior-point solve of the MMA subproblem (Svanberg 2007).

    -> (x, y, z, lam, xsi, eta, mu, zet, s, newton iterations)
    """
    een = np.ones(n)
    eem = np.ones(m)
    epsi = 1.0
    x = 0.5 * (alfa + beta)
    y = eem.copy()
    z = 1.0
    lam = eem.copy()
    xsi = np.maximum(1.0 / (x - alfa), 1.0)
    eta = np.maximum(1.0 / (beta - x), 1.0)
    mu = np.maximum(eem, 0.5 * c)
    zet = 1.0
    s = eem.copy()
    itera = 0

    def residuals(x, y, z, lam, xsi, eta, mu, zet, s, epsi):
        ux1 = upp - x
        xl1 = x - low
        ux2 = ux1 * ux1
        xl2 = xl1 * xl1
        plam = p0 + P.T @ lam
        qlam = q0 + Q.T @ lam
        gvec = P @ (1.0 / ux1) + Q @ (1.0 / xl1)
        dpsidx = plam / ux2 - qlam / xl2
        rex = dpsidx - xsi + eta
        rey = c + d * y - mu - lam
        rez = a0 - zet - a @ lam
        relam = gvec - a * z - y + s - b
        rexsi = xsi * (x - alfa) - epsi
        reeta = eta * (beta - x) - epsi
        remu = mu * y - epsi
        rezet = zet * z - epsi
        res = lam * s - epsi
        residu = np.concatenate([rex, rey, [rez], relam, rexsi, reeta, remu,
                                 [rezet], res])
        return residu

    while epsi > epsimin:
        residu = residuals(x, y, z, lam, xsi, eta, mu, zet, s, epsi)
        residunorm = float(np.linalg.norm(residu))
        residumax = float(np.abs(residu).max())
        ittt = 0
        while residumax > 0.9 * epsi and ittt < 200:
            ittt += 1
            itera += 1
            ux1 = upp - x
            xl1 = x - low
            ux2 = ux1 * ux1
            xl2 = xl1 * xl1
            ux3 = ux1 * ux2
            xl3 = xl1 * xl2
            uxinv1 = 1.0 / ux1
            xlinv1 = 1.0 / xl1
            uxinv2 = 1.0 / ux2
            xlinv2 = 1.0 / xl2
            plam = p0 + P.T @ lam
            qlam = q0 + Q.T @ lam
            gvec = P @ uxinv1 + Q @ xlinv1
            GG = P * uxinv2[None, :] - Q * xlinv2[None, :]
            dpsidx = plam / ux2 - qlam / xl2
            delx = dpsidx - epsi / (x - alfa) + epsi / (beta - x)
            dely = c + d * y - lam - epsi / y
            delz = a0 - a @ lam - epsi / z
            dellam = gvec - a * z - y - b + epsi / lam
            diagx = plam / ux3 + qlam / xl3
            diagx = 2.0 * diagx + xsi / (x - alfa) + eta / (beta - x)
            diagxinv = 1.0 / diagx
            diagy = d + mu / y
            diagyinv = 1.0 / diagy
            diaglam = s / lam
            diaglamyi = diaglam + diagyinv

            if m < n:
                blam = dellam + dely / diagy - GG @ (delx / diagx)
                bb = np.concatenate([blam, [delz]])
                Alam = np.diag(diaglamyi) + (GG * diagxinv[None, :]) @ GG.T
                AA = np.zeros((m + 1, m + 1))
                AA[:m, :m] = Alam
                AA[:m, m] = a
                AA[m, :m] = a
                AA[m, m] = -zet / z
                solut = np.linalg.solve(AA, bb)
                dlam = solut[:m]
                dz = solut[m]
                dx = -delx / diagx - (GG.T @ dlam) / diagx
            else:
                diaglamyiinv = 1.0 / diaglamyi
                dellamyi = dellam + dely / diagy
                Axx = np.diag(diagx) + GG.T @ (diaglamyiinv[:, None] * GG)
                azz = zet / z + a @ (a * diaglamyiinv)
                axz = -GG.T @ (a * diaglamyiinv)
                bx = delx + GG.T @ (dellamyi * diaglamyiinv)
                bz = delz - a @ (dellamyi * diaglamyiinv)
                AA = np.zeros((n + 1, n + 1))
                AA[:n, :n] = Axx
                AA[:n, n] = axz
                AA[n, :n] = axz
                AA[n, n] = azz
                solut = np.linalg.solve(AA, np.concatenate([-bx, [-bz]]))
                dx = solut[:n]
                dz = solut[n]
                dlam = ((GG @ dx) * diaglamyiinv - dz * (a * diaglamyiinv)
                        + dellamyi * diaglamyiinv)

            dy = -dely / diagy + dlam / diagy
            dxsi = -xsi + epsi / (x - alfa) - (xsi * dx) / (x - alfa)
            deta = -eta + epsi / (beta - x) + (eta * dx) / (beta - x)
            dmu = -mu + epsi / y - (mu * dy) / y
            dzet = -zet + epsi / z - zet * dz / z
            ds = -s + epsi / lam - (s * dlam) / lam
            xx = np.concatenate([y, [z], lam, xsi, eta, mu, [zet], s])
            dxx = np.concatenate([dy, [dz], dlam, dxsi, deta, dmu, [dzet], ds])

            stepxx = -1.01 * dxx / xx
            stmxx = stepxx.max()
            stepalfa = -1.01 * dx / (x - alfa)
            stmalfa = stepalfa.max()
            stepbeta = 1.01 * dx / (beta - x)
            stmbeta = stepbeta.max()
            stmalbe = max(stmalfa, stmbeta)
            stmalbexx = max(stmalbe, stmxx)
            stminv = max(stmalbexx, 1.0)
            steg = 1.0 / stminv

            xold, yold, zold, lamold = x, y, z, lam
            xsiold, etaold, muold, zetold, sold = xsi, eta, mu, zet, s
            itto = 0
            resinew = 2.0 * residunorm
            while resinew > residunorm and itto < 50:
                itto += 1
                x = xold + steg * dx
                y = yold + steg * dy
                z = zold + steg * dz
                lam = lamold + steg * dlam
                xsi = xsiold + steg * dxsi
                eta = etaold + steg * deta
                mu = muold + steg * dmu
                zet = zetold + steg * dzet
                s = sold + steg * ds
                residu = residuals(x, y, z, lam, xsi, eta, mu, zet, s, epsi)
                resinew = float(np.linalg.norm(residu))
                steg = steg / 2.0
            residunorm = resinew
            residumax = float(np.abs(residu).max())
        epsi = 0.1 * epsi
    return x, y, z, lam, xsi, eta, mu, zet, s, itera
