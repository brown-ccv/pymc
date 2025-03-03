#   Copyright 2022- The PyMC Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
#   MIT License
#
#   Copyright (c) 2021-2022 aesara-devs
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.

import aesara
import aesara.tensor as at
import numpy as np
import pytest
import scipy as sp
import scipy.stats as st

from pymc.logprob import factorized_joint_logprob, joint_logprob
from pymc.logprob.transforms import LogTransform, TransformValuesRewrite
from pymc.tests.helpers import assert_no_rvs


@aesara.config.change_flags(compute_test_value="raise")
def test_continuous_rv_clip():
    x_rv = at.random.normal(0.5, 1)
    cens_x_rv = at.clip(x_rv, -2, 2)

    cens_x_vv = cens_x_rv.clone()
    cens_x_vv.tag.test_value = 0

    logp = joint_logprob({cens_x_rv: cens_x_vv})
    assert_no_rvs(logp)

    logp_fn = aesara.function([cens_x_vv], logp)
    ref_scipy = st.norm(0.5, 1)

    assert logp_fn(-3) == -np.inf
    assert logp_fn(3) == -np.inf

    assert np.isclose(logp_fn(-2), ref_scipy.logcdf(-2))
    assert np.isclose(logp_fn(2), ref_scipy.logsf(2))
    assert np.isclose(logp_fn(0), ref_scipy.logpdf(0))


def test_discrete_rv_clip():
    x_rv = at.random.poisson(2)
    cens_x_rv = at.clip(x_rv, 1, 4)

    cens_x_vv = cens_x_rv.clone()

    logp = joint_logprob({cens_x_rv: cens_x_vv})
    assert_no_rvs(logp)

    logp_fn = aesara.function([cens_x_vv], logp)
    ref_scipy = st.poisson(2)

    assert logp_fn(0) == -np.inf
    assert logp_fn(5) == -np.inf

    assert np.isclose(logp_fn(1), ref_scipy.logcdf(1))
    assert np.isclose(logp_fn(4), np.logaddexp(ref_scipy.logsf(4), ref_scipy.logpmf(4)))
    assert np.isclose(logp_fn(2), ref_scipy.logpmf(2))


def test_one_sided_clip():
    x_rv = at.random.normal(0, 1)
    lb_cens_x_rv = at.clip(x_rv, -1, x_rv)
    ub_cens_x_rv = at.clip(x_rv, x_rv, 1)

    lb_cens_x_vv = lb_cens_x_rv.clone()
    ub_cens_x_vv = ub_cens_x_rv.clone()

    lb_logp = joint_logprob({lb_cens_x_rv: lb_cens_x_vv})
    ub_logp = joint_logprob({ub_cens_x_rv: ub_cens_x_vv})
    assert_no_rvs(lb_logp)
    assert_no_rvs(ub_logp)

    logp_fn = aesara.function([lb_cens_x_vv, ub_cens_x_vv], [lb_logp, ub_logp])
    ref_scipy = st.norm(0, 1)

    assert np.all(np.array(logp_fn(-2, 2)) == -np.inf)
    assert np.all(np.array(logp_fn(2, -2)) != -np.inf)
    np.testing.assert_almost_equal(logp_fn(-1, 1), ref_scipy.logcdf(-1))
    np.testing.assert_almost_equal(logp_fn(1, -1), ref_scipy.logpdf(-1))


def test_useless_clip():
    x_rv = at.random.normal(0.5, 1, size=3)
    cens_x_rv = at.clip(x_rv, x_rv, x_rv)

    cens_x_vv = cens_x_rv.clone()

    logp = joint_logprob({cens_x_rv: cens_x_vv}, sum=False)
    assert_no_rvs(logp)

    logp_fn = aesara.function([cens_x_vv], logp)
    ref_scipy = st.norm(0.5, 1)

    np.testing.assert_allclose(logp_fn([-2, 0, 2]), ref_scipy.logpdf([-2, 0, 2]))


def test_random_clip():
    lb_rv = at.random.normal(0, 1, size=2)
    x_rv = at.random.normal(0, 2)
    cens_x_rv = at.clip(x_rv, lb_rv, [1, 1])

    lb_vv = lb_rv.clone()
    cens_x_vv = cens_x_rv.clone()
    logp = joint_logprob({cens_x_rv: cens_x_vv, lb_rv: lb_vv}, sum=False)
    assert_no_rvs(logp)

    logp_fn = aesara.function([lb_vv, cens_x_vv], logp)
    res = logp_fn([0, -1], [-1, -1])
    assert res[0] == -np.inf
    assert res[1] != -np.inf


def test_broadcasted_clip_constant():
    lb_rv = at.random.uniform(0, 1)
    x_rv = at.random.normal(0, 2)
    cens_x_rv = at.clip(x_rv, lb_rv, [1, 1])

    lb_vv = lb_rv.clone()
    cens_x_vv = cens_x_rv.clone()

    logp = joint_logprob({cens_x_rv: cens_x_vv, lb_rv: lb_vv})
    assert_no_rvs(logp)


def test_broadcasted_clip_random():
    lb_rv = at.random.normal(0, 1)
    x_rv = at.random.normal(0, 2, size=2)
    cens_x_rv = at.clip(x_rv, lb_rv, 1)

    lb_vv = lb_rv.clone()
    cens_x_vv = cens_x_rv.clone()

    logp = joint_logprob({cens_x_rv: cens_x_vv, lb_rv: lb_vv})
    assert_no_rvs(logp)


def test_fail_base_and_clip_have_values():
    """Test failure when both base_rv and clipped_rv are given value vars"""
    x_rv = at.random.normal(0, 1)
    cens_x_rv = at.clip(x_rv, x_rv, 1)
    cens_x_rv.name = "cens_x"

    x_vv = x_rv.clone()
    cens_x_vv = cens_x_rv.clone()
    with pytest.raises(RuntimeError, match="could not be derived: {cens_x}"):
        factorized_joint_logprob({cens_x_rv: cens_x_vv, x_rv: x_vv})


def test_fail_multiple_clip_single_base():
    """Test failure when multiple clipped_rvs share a single base_rv"""
    base_rv = at.random.normal(0, 1)
    cens_rv1 = at.clip(base_rv, -1, 1)
    cens_rv1.name = "cens1"
    cens_rv2 = at.clip(base_rv, -1, 1)
    cens_rv2.name = "cens2"

    cens_vv1 = cens_rv1.clone()
    cens_vv2 = cens_rv2.clone()
    with pytest.raises(RuntimeError, match="could not be derived: {cens2}"):
        factorized_joint_logprob({cens_rv1: cens_vv1, cens_rv2: cens_vv2})


def test_deterministic_clipping():
    x_rv = at.random.normal(0, 1)
    clip = at.clip(x_rv, 0, 0)
    y_rv = at.random.normal(clip, 1)

    x_vv = x_rv.clone()
    y_vv = y_rv.clone()
    logp = joint_logprob({x_rv: x_vv, y_rv: y_vv})
    assert_no_rvs(logp)

    logp_fn = aesara.function([x_vv, y_vv], logp)
    assert np.isclose(
        logp_fn(-1, 1),
        st.norm(0, 1).logpdf(-1) + st.norm(0, 1).logpdf(1),
    )


def test_clip_transform():
    x_rv = at.random.normal(0.5, 1)
    cens_x_rv = at.clip(x_rv, 0, x_rv)

    cens_x_vv = cens_x_rv.clone()

    transform = TransformValuesRewrite({cens_x_vv: LogTransform()})
    logp = joint_logprob({cens_x_rv: cens_x_vv}, extra_rewrites=transform)

    cens_x_vv_testval = -1
    obs_logp = logp.eval({cens_x_vv: cens_x_vv_testval})
    exp_logp = sp.stats.norm(0.5, 1).logpdf(np.exp(cens_x_vv_testval)) + cens_x_vv_testval

    assert np.isclose(obs_logp, exp_logp)


@pytest.mark.parametrize("rounding_op", (at.round, at.floor, at.ceil))
def test_rounding(rounding_op):
    loc = 1
    scale = 2
    test_value = np.arange(-3, 4)

    x = at.random.normal(loc, scale, size=test_value.shape, name="x")
    xr = rounding_op(x)
    xr.name = "xr"

    xr_vv = xr.clone()
    logp = joint_logprob({xr: xr_vv}, sum=False)
    assert logp is not None

    x_sp = st.norm(loc, scale)
    if rounding_op == at.round:
        expected_logp = np.log(x_sp.cdf(test_value + 0.5) - x_sp.cdf(test_value - 0.5))
    elif rounding_op == at.floor:
        expected_logp = np.log(x_sp.cdf(test_value + 1.0) - x_sp.cdf(test_value))
    elif rounding_op == at.ceil:
        expected_logp = np.log(x_sp.cdf(test_value) - x_sp.cdf(test_value - 1.0))
    else:
        raise NotImplementedError()

    assert np.allclose(
        logp.eval({xr_vv: test_value}),
        expected_logp,
    )
