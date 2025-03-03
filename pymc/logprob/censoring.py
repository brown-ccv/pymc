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

from typing import List, Optional

import aesara.tensor as at
import numpy as np

from aesara.graph.basic import Node
from aesara.graph.fg import FunctionGraph
from aesara.graph.rewriting.basic import node_rewriter
from aesara.scalar.basic import Ceil, Clip, Floor, RoundHalfToEven
from aesara.scalar.basic import clip as scalar_clip
from aesara.tensor.elemwise import Elemwise
from aesara.tensor.var import TensorConstant

from pymc.logprob.abstract import (
    MeasurableElemwise,
    MeasurableVariable,
    _logcdf,
    _logprob,
    assign_custom_measurable_outputs,
)
from pymc.logprob.rewriting import measurable_ir_rewrites_db
from pymc.logprob.utils import CheckParameterValue


class MeasurableClip(MeasurableElemwise):
    """A placeholder used to specify a log-likelihood for a clipped RV sub-graph."""

    valid_scalar_types = (Clip,)


measurable_clip = MeasurableClip(scalar_clip)


@node_rewriter(tracks=[Elemwise])
def find_measurable_clips(fgraph: FunctionGraph, node: Node) -> Optional[List[MeasurableClip]]:
    # TODO: Canonicalize x[x>ub] = ub -> clip(x, x, ub)

    rv_map_feature = getattr(fgraph, "preserve_rv_mappings", None)
    if rv_map_feature is None:
        return None  # pragma: no cover

    if isinstance(node.op, MeasurableClip):
        return None  # pragma: no cover

    if not (isinstance(node.op, Elemwise) and isinstance(node.op.scalar_op, Clip)):
        return None

    clipped_var = node.outputs[0]
    base_var, lower_bound, upper_bound = node.inputs

    if not (
        base_var.owner
        and isinstance(base_var.owner.op, MeasurableVariable)
        and base_var not in rv_map_feature.rv_values
    ):
        return None

    # Replace bounds by `+-inf` if `y = clip(x, x, ?)` or `y=clip(x, ?, x)`
    # This is used in `clip_logprob` to generate a more succint logprob graph
    # for one-sided clipped random variables
    lower_bound = lower_bound if (lower_bound is not base_var) else at.constant(-np.inf)
    upper_bound = upper_bound if (upper_bound is not base_var) else at.constant(np.inf)

    # Make base_var unmeasurable
    unmeasurable_base_var = assign_custom_measurable_outputs(base_var.owner)
    clipped_rv_node = measurable_clip.make_node(unmeasurable_base_var, lower_bound, upper_bound)
    clipped_rv = clipped_rv_node.outputs[0]

    clipped_rv.name = clipped_var.name

    return [clipped_rv]


measurable_ir_rewrites_db.register(
    "find_measurable_clips",
    find_measurable_clips,
    0,
    "basic",
    "censoring",
)


@_logprob.register(MeasurableClip)
def clip_logprob(op, values, base_rv, lower_bound, upper_bound, **kwargs):
    r"""Logprob of a clipped censored distribution

    The probability is given by
    .. math::
        \begin{cases}
            0 & \text{for } x < lower, \\
            \text{CDF}(lower, dist) & \text{for } x = lower, \\
            \text{P}(x, dist) & \text{for } lower < x < upper, \\
            1-\text{CDF}(upper, dist) & \text {for} x = upper, \\
            0 & \text{for } x > upper,
        \end{cases}

    """
    (value,) = values

    base_rv_op = base_rv.owner.op
    base_rv_inputs = base_rv.owner.inputs

    logprob = _logprob(base_rv_op, (value,), *base_rv_inputs, **kwargs)
    logcdf = _logcdf(base_rv_op, value, *base_rv_inputs, **kwargs)

    if base_rv_op.name:
        logprob.name = f"{base_rv_op}_logprob"
        logcdf.name = f"{base_rv_op}_logcdf"

    is_lower_bounded, is_upper_bounded = False, False
    if not (isinstance(upper_bound, TensorConstant) and np.all(np.isinf(upper_bound.value))):
        is_upper_bounded = True

        logccdf = at.log1mexp(logcdf)
        # For right clipped discrete RVs, we need to add an extra term
        # corresponding to the pmf at the upper bound
        if base_rv.dtype.startswith("int"):
            logccdf = at.logaddexp(logccdf, logprob)

        logprob = at.switch(
            at.eq(value, upper_bound),
            logccdf,
            at.switch(at.gt(value, upper_bound), -np.inf, logprob),
        )
    if not (isinstance(lower_bound, TensorConstant) and np.all(np.isneginf(lower_bound.value))):
        is_lower_bounded = True
        logprob = at.switch(
            at.eq(value, lower_bound),
            logcdf,
            at.switch(at.lt(value, lower_bound), -np.inf, logprob),
        )

    if is_lower_bounded and is_upper_bounded:
        logprob = CheckParameterValue("lower_bound <= upper_bound")(
            logprob, at.all(at.le(lower_bound, upper_bound))
        )

    return logprob


class MeasurableRound(MeasurableElemwise):
    """A placeholder used to specify a log-likelihood for a clipped RV sub-graph."""

    valid_scalar_types = (RoundHalfToEven, Floor, Ceil)


@node_rewriter(tracks=[Elemwise])
def find_measurable_roundings(fgraph: FunctionGraph, node: Node) -> Optional[List[MeasurableRound]]:

    rv_map_feature = getattr(fgraph, "preserve_rv_mappings", None)
    if rv_map_feature is None:
        return None  # pragma: no cover

    if isinstance(node.op, MeasurableRound):
        return None  # pragma: no cover

    if not (
        isinstance(node.op, Elemwise)
        and isinstance(node.op.scalar_op, MeasurableRound.valid_scalar_types)
    ):
        return None

    (rounded_var,) = node.outputs
    (base_var,) = node.inputs

    if not (
        base_var.owner
        and isinstance(base_var.owner.op, MeasurableVariable)
        and base_var not in rv_map_feature.rv_values
        # Rounding only makes sense for continuous variables
        and base_var.dtype.startswith("float")
    ):
        return None

    # Make base_var unmeasurable
    unmeasurable_base_var = assign_custom_measurable_outputs(base_var.owner)

    rounded_op = MeasurableRound(node.op.scalar_op)
    rounded_rv = rounded_op.make_node(unmeasurable_base_var).default_output()
    rounded_rv.name = rounded_var.name
    return [rounded_rv]


measurable_ir_rewrites_db.register(
    "find_measurable_roundings",
    find_measurable_roundings,
    0,
    "basic",
    "censoring",
)


@_logprob.register(MeasurableRound)
def round_logprob(op, values, base_rv, **kwargs):
    r"""Logprob of a rounded censored distribution

    The probability of a distribution rounded to the nearest integer is given by
    .. math::
        \begin{cases}
            \text{CDF}(x+\frac{1}{2}, dist) - \text{CDF}(x-\frac{1}{2}, dist) & \text{for } x \in \mathbb{Z}, \\
            0 & \text{otherwise},
        \end{cases}

    The probability of a distribution rounded up is given by
    .. math::
        \begin{cases}
            \text{CDF}(x, dist) - \text{CDF}(x-1, dist) & \text{for } x \in \mathbb{Z}, \\
            0 & \text{otherwise},
        \end{cases}

    The probability of a distribution rounded down is given by
    .. math::
        \begin{cases}
            \text{CDF}(x+1, dist) - \text{CDF}(x, dist) & \text{for } x \in \mathbb{Z}, \\
            0 & \text{otherwise},
        \end{cases}

    """
    (value,) = values

    if isinstance(op.scalar_op, RoundHalfToEven):
        value = at.round(value)
        value_upper = value + 0.5
        value_lower = value - 0.5
    elif isinstance(op.scalar_op, Floor):
        value = at.floor(value)
        value_upper = value + 1.0
        value_lower = value
    elif isinstance(op.scalar_op, Ceil):
        value = at.ceil(value)
        value_upper = value
        value_lower = value - 1.0
    else:
        raise TypeError(f"Unsupported scalar_op {op.scalar_op}")  # pragma: no cover

    base_rv_op = base_rv.owner.op
    base_rv_inputs = base_rv.owner.inputs

    logcdf_upper = _logcdf(base_rv_op, value_upper, *base_rv_inputs, **kwargs)
    logcdf_lower = _logcdf(base_rv_op, value_lower, *base_rv_inputs, **kwargs)

    if base_rv_op.name:
        logcdf_upper.name = f"{base_rv_op}_logcdf_upper"
        logcdf_lower.name = f"{base_rv_op}_logcdf_lower"

    # TODO: Figure out better solution to avoid this circular import
    from pymc.math import logdiffexp

    return logdiffexp(logcdf_upper, logcdf_lower)
