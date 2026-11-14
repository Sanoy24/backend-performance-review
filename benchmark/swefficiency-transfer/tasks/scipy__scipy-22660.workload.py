import timeit
import statistics

import numpy as np
from scipy import sparse
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

def coherent_linear_quantile_regression(
    X,
    y,
    *,
    quantiles,
    sample_weight = None,
    coherence_buffer = 3,
):
    """Solve a Coherent Linear Quantile Regression problem.

    Minimizes the quantile loss:

        ∑ᵢ,ⱼ {
                 qⱼ (yᵢ - ŷ⁽ʲ⁾ᵢ) : yᵢ ≥ ŷ⁽ʲ⁾ᵢ,
            (1 - qⱼ)(ŷ⁽ʲ⁾ᵢ - yᵢ) : ŷ⁽ʲ⁾ᵢ > yᵢ
        }

    for the linear model ŷ⁽ʲ⁾ := Xβ⁽ʲ⁾, given an input dataset X, target y, and quantile ranks qⱼ.

    We achieve so-called 'coherent' quantiles by enforcing monotonicity of the predicted quantiles
    with the constraint Xβ⁽ʲ⁾ ≤ Xβ⁽ʲ⁺¹⁾ for each consecutive pair of quantile ranks in an extended
    set of quantile ranks that comprises the requested quantile ranks and a number of auxiliary
    quantile ranks in between.

    The optimization problem is formulated as a linear program by introducing the auxiliary residual
    vectors Δ⁽ʲ⁾⁺, Δ⁽ʲ⁾⁻ ≥ 0 so that Xβ⁽ʲ⁾ - y = Δ⁽ʲ⁾⁺ - Δ⁽ʲ⁾⁻. The objective then becomes
    ∑ᵢ,ⱼ qⱼΔ⁽ʲ⁾⁻ᵢ + (1 - qⱼ)Δ⁽ʲ⁾⁺ᵢ + αt⁽ʲ⁾ᵢ for t⁽ʲ⁾ := |β⁽ʲ⁾|. The L1 regularization parameter α is
    automatically determined to minimize the impact on the solution β.

    Parameters
    ----------
    X
        The feature matrix.
    y
        The target values.
    quantiles
        The quantiles to estimate (between 0 and 1).
    sample_weight
        The optional sample weight to use for each sample.
    coherence_buffer
        The number of auxiliary quantiles to introduce. Smaller is faster, larger yields more
        coherent quantiles.

    Returns
    -------
    β
        The estimated regression coefficients so that Xβ produces quantile predictions ŷ.
    β_full
        The estimated regression coefficients including all auxiliary quantiles.
    """
    # Learn the input dimensions.
    num_samples, num_features = X.shape
    # Add buffer quantile ranks in between the given quantile ranks so that we have an even stronger
    # guarantee on the monotonicity of the predicted quantiles.
    quantiles = np.interp(
        x=np.linspace(0, len(quantiles) - 1, (len(quantiles) - 1) * (1 + coherence_buffer) + 1),
        xp=np.arange(len(quantiles)),
        fp=quantiles,
    ).astype(quantiles.dtype)
    num_quantiles = len(quantiles)
    # Validate the input.
    assert np.array_equal(quantiles, np.sort(quantiles)), "Quantile ranks must be sorted."
    assert sample_weight is None or np.all(sample_weight >= 0), "Sample weights must be >= 0."
    # Normalise the sample weights.
    sample_weight = np.ones(num_samples, dtype=y.dtype) if sample_weight is None else sample_weight
    sample_weight /= np.sum(sample_weight)
    eps = np.finfo(y.dtype).eps
    α = eps**0.25 / (num_quantiles * num_features)
    # Construct the objective function ∑ᵢ,ⱼ qⱼΔ⁽ʲ⁾⁻ᵢ + (1 - qⱼ)Δ⁽ʲ⁾⁺ᵢ + αt⁽ʲ⁾ᵢ for t⁽ʲ⁾ := |β⁽ʲ⁾|.
    c = np.hstack(
        [
            np.zeros(num_quantiles * num_features, dtype=y.dtype),  # β⁽ʲ⁾ for each qⱼ
            α * np.ones(num_quantiles * num_features, dtype=y.dtype),  # t⁽ʲ⁾ for each qⱼ
            np.kron((1 - quantiles) / num_quantiles, sample_weight),  # Δ⁽ʲ⁾⁺ for each qⱼ
            np.kron(quantiles / num_quantiles, sample_weight),  # Δ⁽ʲ⁾⁻ for each qⱼ
        ]
    )
    # Construct the equalities Xβ⁽ʲ⁾ - y = Δ⁽ʲ⁾⁺ - Δ⁽ʲ⁾⁻ for each quantile rank qⱼ.
    A_eq = sparse.hstack(
        [
            # Xβ⁽ʲ⁾ for each qⱼ (block diagonal matrix)
            sparse.kron(sparse.eye(num_quantiles, dtype=X.dtype), X),
            # t⁽ʲ⁾ not used in this constraint
            csr_matrix((num_quantiles * num_samples, num_quantiles * num_features), dtype=X.dtype),
            # -Δ⁽ʲ⁾⁺ for each qⱼ (block diagonal matrix)
            -sparse.eye(num_quantiles * num_samples, dtype=X.dtype),
            # Δ⁽ʲ⁾⁻ for each qⱼ (block diagonal matrix)
            sparse.eye(num_quantiles * num_samples, dtype=X.dtype),
        ]
    )
    b_eq = np.tile(y, num_quantiles)
    # Construct the inequalities -t⁽ʲ⁾ <= β⁽ʲ⁾ <= t⁽ʲ⁾ for each quantile rank qⱼ so that
    # t⁽ʲ⁾ := |β⁽ʲ⁾|. Also construct the monotonicity constraint Xβ⁽ʲ⁾ <= Xβ⁽ʲ⁺¹⁾ for each qⱼ,
    # equivalent to Δ⁽ʲ⁾⁺ - Δ⁽ʲ⁾⁻ <= Δ⁽ʲ⁺¹⁾⁺ - Δ⁽ʲ⁺¹⁾⁻.
    zeros_Δ = csr_matrix(
        (num_quantiles * num_features, 2 * num_quantiles * num_samples), dtype=X.dtype
    )
    zeros_βt = csr_matrix(
        ((num_quantiles - 1) * num_samples, 2 * num_quantiles * num_features), dtype=X.dtype
    )
    A_ub = sparse.vstack(
        [
            sparse.hstack(
                [
                    sparse.eye(num_quantiles * num_features, dtype=X.dtype),  # β⁽ʲ⁾
                    -sparse.eye(num_quantiles * num_features, dtype=X.dtype),  # -t⁽ʲ⁾
                    zeros_Δ,  # Δ⁽ʲ⁾⁺ and Δ⁽ʲ⁾⁺ not used for this constraint
                ]
            ),
            sparse.hstack(
                [
                    -sparse.eye(num_quantiles * num_features, dtype=X.dtype),  # -β⁽ʲ⁾
                    -sparse.eye(num_quantiles * num_features, dtype=X.dtype),  # -t⁽ʲ⁾
                    zeros_Δ,  # Δ⁽ʲ⁾⁺ and Δ⁽ʲ⁾⁺ not used for this constraint
                ]
            ),
            sparse.hstack(
                [
                    zeros_βt,
                    sparse.kron(
                        sparse.diags(
                            diagonals=[1, -1],  # Δ⁽ʲ⁾⁺ - Δ⁽ʲ⁺¹⁾⁺
                            offsets=[0, 1],
                            shape=(num_quantiles - 1, num_quantiles),
                            dtype=X.dtype,
                        ),
                        sparse.eye(num_samples, dtype=X.dtype),
                    ),
                    sparse.kron(
                        sparse.diags(
                            diagonals=[-1, 1],  # -Δ⁽ʲ⁾⁻ + Δ⁽ʲ⁺¹⁾⁻
                            offsets=[0, 1],
                            shape=(num_quantiles - 1, num_quantiles),
                            dtype=X.dtype,
                        ),
                        sparse.eye(num_samples, dtype=X.dtype),
                    ),
                ]
            ),
        ]
    )
    b_ub = np.zeros(A_ub.shape[0], dtype=X.dtype)
    # Construct the bounds.
    bounds = (
        ([(None, None)] * num_quantiles * num_features)  # β⁽ʲ⁾ for each qⱼ
        + ([(0, None)] * num_quantiles * num_features)  # t⁽ʲ⁾ for each qⱼ
        + ([(0, None)] * num_quantiles * num_samples)  # Δ⁽ʲ⁾⁺ for each qⱼ
        + ([(0, None)] * num_quantiles * num_samples)  # Δ⁽ʲ⁾⁻ for each qⱼ
    )
    # Solve the Coherent Quantile Regression LP.
    result = linprog(c=c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    # Extract the solution.
    β_full = result.x[: num_quantiles * num_features].astype(y.dtype)
    β_full = β_full.reshape(num_quantiles, num_features).T
    # Drop the buffer quantile ranks we introduced earlier.
    β = β_full[:, 0 :: (coherence_buffer + 1)]
    return β, β_full

np.random.seed(42)
n, d = 500, 5
X = np.random.randn(n, d)
y = np.random.randn(n)
quantiles = np.asarray((0.25, 0.5, 0.75))

def workload():
    coherent_linear_quantile_regression(X, y, quantiles=quantiles)

runtimes = timeit.repeat(workload, number=1, repeat=3)

print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))

