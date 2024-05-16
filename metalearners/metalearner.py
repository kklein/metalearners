# Copyright (c) QuantCo 2024-2024
# SPDX-License-Identifier: LicenseRef-QuantCo

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection
from typing import TypedDict

import numpy as np
from typing_extensions import Self

from metalearners._utils import Matrix, Vector, _ScikitModel, validate_number_positive
from metalearners.cross_fit_estimator import (
    OVERALL,
    CrossFitEstimator,
    OosMethod,
    PredictMethod,
)

Params = dict[str, int | float | str]
Features = Collection[str] | Collection[int]
ModelFactory = type[_ScikitModel] | dict[str, type[_ScikitModel]]
PROPENSITY_MODEL = "propensity_model"


def _initialize_model_dict(argument, expected_names: Collection[str]) -> dict:
    if isinstance(argument, dict) and set(argument.keys()) == set(expected_names):
        return argument
    return {name: argument for name in expected_names}


def _combine_propensity_and_nuisance_specs(
    propensity_specs, nuisance_specs, nuisance_model_names: set[str]
) -> dict:
    if PROPENSITY_MODEL in nuisance_model_names:
        non_propensity_nuisance_model_names = nuisance_model_names - {PROPENSITY_MODEL}
        non_propensity_model_dict = _initialize_model_dict(
            nuisance_specs, non_propensity_nuisance_model_names
        )
        return non_propensity_model_dict | {PROPENSITY_MODEL: propensity_specs}

    return _initialize_model_dict(nuisance_specs, nuisance_model_names)


class _ModelSpecifications(TypedDict):
    # The quotes on MetaLearner are necessary for type hinting as it's not yet defined
    # here. Check https://stackoverflow.com/questions/55320236/does-python-evaluate-type-hinting-of-a-forward-reference
    # At some point evaluation at runtime will be the default and then this won't be needed.
    cardinality: Callable[["MetaLearner"], int]
    predict_method: Callable[["MetaLearner"], PredictMethod]


class MetaLearner(ABC):

    @classmethod
    @abstractmethod
    def nuisance_model_specifications(cls) -> dict[str, _ModelSpecifications]: ...

    @classmethod
    @abstractmethod
    def treatment_model_specifications(cls) -> dict[str, _ModelSpecifications]: ...

    def _validate_params(self, **kwargs): ...

    @classmethod
    @abstractmethod
    def _supports_multi_treatment(cls) -> bool: ...

    @classmethod
    @abstractmethod
    def _supports_multi_class(cls) -> bool: ...

    @classmethod
    def _check_n_variants(cls, n_variants: int) -> None:
        if not isinstance(n_variants, int) or n_variants < 2:
            raise ValueError(
                "n_variants needs to be an integer strictly greater than 1."
            )
        if n_variants > 2 and not cls._supports_multi_treatment():
            raise NotImplementedError(
                f"Current implementation of {cls.__name__} only supports binary "
                f"treatment variants. Yet, n_variants was set to {n_variants}."
            )

    def _check_treatment(self, w: Vector) -> None:
        if len(np.unique(w)) != self.n_variants:
            raise ValueError(
                "Number of variants present in the treatment are different than the "
                "number specified at instantiation."
            )
        # TODO: add support for different encoding of treatment variants (str, not consecutive ints...)
        if set(np.unique(w)) != set(range(self.n_variants)):
            raise ValueError(
                "Treatment variant should be encoded with values "
                f"{{0...{self.n_variants -1}}} and all variants should be present. "
                f"Yet we found the values {set(np.unique(w))}."
            )

    def _check_outcome(self, y: Vector) -> None:
        if (
            self.is_classification
            and not self._supports_multi_class()
            and len(np.unique(y)) > 2
        ):
            raise ValueError(
                f"{self.__class__.__name__} does not support multiclass classification."
                f" Yet we found {len(np.unique(y))} classes."
            )

    @abstractmethod
    def _validate_models(self) -> None:
        """Validate that the models are of the correct type (classifier or regressor)"""
        ...

    def __init__(
        self,
        nuisance_model_factory: ModelFactory,
        is_classification: bool,
        # TODO: Consider whether we can make this not a state of the MetaLearner
        # but rather just a parameter of a predict call.
        n_variants: int,
        treatment_model_factory: ModelFactory | None = None,
        propensity_model_factory: type[_ScikitModel] | None = None,
        nuisance_model_params: Params | dict[str, Params] | None = None,
        treatment_model_params: Params | dict[str, Params] | None = None,
        propensity_model_params: Params | None = None,
        feature_set: Features | dict[str, Features] | None = None,
        # TODO: Consider implementing selection of number of folds for various estimators.
        n_folds: int = 10,
        random_state: int | None = None,
    ):
        """Initialize a MetaLearner.

        All of
        * ``nuisance_model_factory``
        * ``treatment_model_factory``
        * ``nuisance_model_params``
        * ``treatment_model_params``
        * ``feature_set``

        can either

        * contain a single value, such that the value will be used for all relevant models
        of the respective MetaLearner or
        * a dictionary mapping from the relevant models (``model_kind``, a ``str``) to the
        respective value
        """
        self._validate_params(
            nuisance_model_factory=nuisance_model_factory,
            treatment_model_factory=treatment_model_factory,
            propensity_model_factory=propensity_model_factory,
            is_classification=is_classification,
            n_variants=n_variants,
            nuisance_model_params=nuisance_model_params,
            treatment_model_params=treatment_model_params,
            propensity_model_params=propensity_model_params,
            feature_set=feature_set,
            n_folds=n_folds,
            random_state=random_state,
        )

        nuisance_model_specifications = self.nuisance_model_specifications()
        treatment_model_specifications = self.treatment_model_specifications()

        if PROPENSITY_MODEL in treatment_model_specifications:
            raise ValueError(
                f"{PROPENSITY_MODEL} can't be used as a treatment model name"
            )
        if (
            isinstance(nuisance_model_factory, dict)
            and PROPENSITY_MODEL in nuisance_model_factory.keys()
        ):
            raise ValueError(
                "Propensity model factory should be defined using propensity_model_factory "
                "and not nuisance_model_factory."
            )
        if (
            isinstance(nuisance_model_params, dict)
            and PROPENSITY_MODEL in nuisance_model_params.keys()
        ):
            raise ValueError(
                "Propensity model params should be defined using propensity_model_params "
                "and not nuisance_model_params."
            )
        if (
            PROPENSITY_MODEL in nuisance_model_specifications
            and propensity_model_factory is None
        ):
            raise ValueError(
                f"propensity_model_factory needs to be defined as the {self.__class__.__name__}"
                " has a propensity model."
            )

        self._check_n_variants(n_variants)
        self.is_classification = is_classification
        self.n_variants = n_variants

        self.nuisance_model_factory = _combine_propensity_and_nuisance_specs(
            propensity_model_factory,
            nuisance_model_factory,
            set(nuisance_model_specifications.keys()),
        )
        if nuisance_model_params is None:
            nuisance_model_params = {}  # type: ignore
        if propensity_model_params is None:
            propensity_model_params = {}
        self.nuisance_model_params = _combine_propensity_and_nuisance_specs(
            propensity_model_params,
            nuisance_model_params,
            set(nuisance_model_specifications.keys()),
        )

        self.treatment_model_factory = _initialize_model_dict(
            treatment_model_factory, set(treatment_model_specifications.keys())
        )
        if treatment_model_params is None:
            self.treatment_model_params = _initialize_model_dict(
                {}, set(treatment_model_specifications.keys())
            )
        else:
            self.treatment_model_params = _initialize_model_dict(
                treatment_model_params, set(treatment_model_specifications.keys())
            )

        validate_number_positive(n_folds, "n_folds")
        self.n_folds = n_folds
        self.random_state = random_state

        if feature_set is None:
            self.feature_set = None
        else:
            self.feature_set = _initialize_model_dict(
                feature_set,
                set(nuisance_model_specifications.keys())
                | set(treatment_model_specifications.keys()),
            )

        self._nuisance_models: dict[str, list[CrossFitEstimator]] = {
            name: [
                CrossFitEstimator(
                    n_folds=self.n_folds,
                    estimator_factory=self.nuisance_model_factory[name],
                    estimator_params=self.nuisance_model_params[name],
                    random_state=self.random_state,
                )
                for _ in range(nuisance_model_specifications[name]["cardinality"](self))
            ]
            for name in set(nuisance_model_specifications.keys())
        }
        self._treatment_models: dict[str, list[CrossFitEstimator]] = {
            name: [
                CrossFitEstimator(
                    n_folds=self.n_folds,
                    estimator_factory=self.treatment_model_factory[name],
                    estimator_params=self.treatment_model_params[name],
                    random_state=self.random_state,
                )
                for _ in range(
                    treatment_model_specifications[name]["cardinality"](self)
                )
            ]
            for name in set(treatment_model_specifications.keys())
        }

        self._validate_models()

    def _nuisance_tensors(self, n_obs: int) -> dict[str, list[np.ndarray]]:
        def dimension(n_obs, model_kind, model_ord, predict_method):
            if (
                n_outputs := self._nuisance_models[model_kind][model_ord]._n_outputs(
                    predict_method
                )
            ) > 1:
                return (n_obs, n_outputs)
            return (n_obs,)

        nuisance_tensors: dict[str, list[np.ndarray]] = {}
        for (
            model_kind,
            model_specifications,
        ) in self.nuisance_model_specifications().items():
            nuisance_tensors[model_kind] = []
            for model_ord in range(model_specifications["cardinality"](self)):
                nuisance_tensors[model_kind].append(
                    np.zeros(
                        dimension(
                            n_obs,
                            model_kind,
                            model_ord,
                            model_specifications["predict_method"](self),
                        )
                    )
                )
        return nuisance_tensors

    def fit_nuisance(
        self,
        X: Matrix,
        y: Vector,
        model_kind: str,
        model_ord: int,
        fit_params: dict | None = None,
    ) -> Self:
        """Fit a given nuisance model of a MetaLearner.

        ``y`` represents the objective of the given nuisance model, not necessarily the outcome of the experiment.
        """
        X_filtered = X[self.feature_set[model_kind]] if self.feature_set else X
        self._nuisance_models[model_kind][model_ord].fit(
            X_filtered, y, fit_params=fit_params
        )
        return self

    def fit_treatment(
        self,
        X: Matrix,
        y: Vector,
        model_kind: str,
        model_ord: int,
        fit_params: dict | None = None,
    ) -> Self:
        """Fit the treatment model of a MetaLearner.

        ``y`` represents the objective of the given treatment model, not necessarily the outcome of the experiment.
        """
        X_filtered = X[self.feature_set[model_kind]] if self.feature_set else X
        self._treatment_models[model_kind][model_ord].fit(
            X_filtered, y, fit_params=fit_params
        )
        return self

    @abstractmethod
    def fit(self, X: Matrix, y: Vector, w: Vector) -> Self:
        """Fit all models of a MetaLearner."""
        ...

    def predict_nuisance(
        self,
        X: Matrix,
        model_kind: str,
        model_ord: int,
        is_oos: bool,
        oos_method: OosMethod = OVERALL,
    ) -> np.ndarray:
        """Estimate based on a given nuisance model.

        Importantly, this method needs to implement the subselection of ``X`` based on
        the ``feature_set`` field of ``MetaLearner``.
        """
        X_filtered = X[self.feature_set[model_kind]] if self.feature_set else X
        predict_method_name = self.nuisance_model_specifications()[model_kind][
            "predict_method"
        ](self)
        predict_method = getattr(
            self._nuisance_models[model_kind][model_ord], predict_method_name
        )
        return predict_method(X_filtered, is_oos, oos_method)

    def predict_treatment(
        self,
        X: Matrix,
        model_kind: str,
        model_ord: int,
        is_oos: bool,
        oos_method: OosMethod = OVERALL,
    ) -> np.ndarray:
        """Estimate based on a given treatment model.

        Importantly, this method needs to implement the subselection of ``X`` based on
        the ``feature_set`` field of ``MetaLearner``.
        """
        X_filtered = X[self.feature_set[model_kind]] if self.feature_set else X
        return self._treatment_models[model_kind][model_ord].predict(
            X_filtered, is_oos, oos_method
        )

    @abstractmethod
    def predict(
        self,
        X: Matrix,
        is_oos: bool,
        oos_method: OosMethod = OVERALL,
    ) -> np.ndarray:
        """Estimate the Conditional Average Treatment Effect.

        This method can be identical to predict_treatment but doesn't need to.
        """
        ...

    @abstractmethod
    def evaluate(
        self,
        X: Matrix,
        y: Vector,
        w: Vector,
        is_oos: bool,
        oos_method: OosMethod = OVERALL,
    ) -> dict[str, float | int]:
        """Evaluate all models contained in a MetaLearner."""
        ...
