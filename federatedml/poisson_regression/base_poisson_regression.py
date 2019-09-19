#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import numpy as np
from google.protobuf import json_format

from federatedml.protobuf.generated import poisson_model_meta_pb2, poisson_model_param_pb2
from arch.api.utils import log_utils
from fate_flow.entity.metric import Metric
from fate_flow.entity.metric import MetricMeta
from federatedml.poisson_regression.poisson_regression_weights import PoissonRegressionWeights
from federatedml.model_base import ModelBase
from federatedml.model_selection.KFold import KFold
from federatedml.optim.initialize import Initializer
from federatedml.optim.convergence import converge_func_factory
from federatedml.optim.optimizer import optimizer_factory

from federatedml.param.poisson_regression_param import PoissonParam
from federatedml.secureprotol import PaillierEncrypt, FakeEncrypt
from federatedml.statistic import data_overview
from federatedml.util import consts
from federatedml.util import abnormal_detection



LOGGER = log_utils.getLogger()


class BasePoissonRegression(ModelBase):
    def __init__(self):
        super(BasePoissonRegression, self).__init__()
        self.model_param = PoissonParam()
        # attribute:
        self.n_iter_ = 0
        self.coef_ = None
        self.intercept_ = 0
        self.classes_ = None
        self.feature_shape = None

        self.gradient_operator = None
        self.initializer = Initializer()
        self.transfer_variable = None
        self.loss_history = []
        self.is_converged = False
        self.header = None
        self.class_name = self.__class__.__name__
        self.model_name = 'PoissonRegression'
        self.model_param_name = 'PoissonRegressionParam'
        self.model_meta_name = 'PoissonRegressionMeta'
        self.role = ''
        self.mode = ''
        self.schema = {}
        self.cipher_operator = None

    def _init_model(self, params):
        self.model_param = params
        self.alpha = params.alpha
        self.init_param_obj = params.init_param
        self.fit_intercept = self.init_param_obj.fit_intercept
        self.encrypted_mode_calculator_param = params.encrypted_mode_calculator_param
        self.encrypted_calculator = None

        self.batch_size = params.batch_size
        self.max_iter = params.max_iter
        self.party_weight = params.party_weight
        self.optimizer = optimizer_factory(params)

        self.exposure_index = params.exposure_index

        if params.encrypt_param.method == consts.PAILLIER:
            self.cipher_operator = PaillierEncrypt()
        else:
            self.cipher_operator = FakeEncrypt()

        self.converge_func = converge_func_factory(params)
        self.re_encrypt_batches = params.re_encrypt_batches

    def set_feature_shape(self, feature_shape):
        self.feature_shape = feature_shape

    def set_header(self, header):
        self.header = header

    def get_features_shape(self, data_instances):
        if self.feature_shape is not None:
            return self.feature_shape
        return data_overview.get_features_shape(data_instances)

    def get_header(self, data_instances):
        if self.header is not None:
            return self.header
        return data_instances.schema.get("header")

    def load_instance(self, data_instance):
        """
        return data_instance without exposure
        Parameters
        ----------
        data_instance: DTable of Instances, input data
        """
        if self.exposure_index == -1:
            return data_instance
        if self.exposure_index >= len(data_instance.features):
            raise ValueError(
                "exposure_index {} out of features' range".format(self.exposure_index))
        data_instance.features = data_instance.features.pop(self.exposure_index)
        return data_instance

    def load_exposure(self, data_instance):
        """
        return exposure of a given data_instance
        Parameters
        ----------
        data_instance: DTable of Instances, input data
        """
        if self.exposure_index == -1:
            exposure = 1
        else:
            exposure = data_instance.features[self.exposure_index]
        return exposure

    def callback_loss(self, iter_num, loss):
        metric_meta = MetricMeta(name='train',
                                     metric_type="LOSS",
                                     extra_metas={
                                         "unit_name": "iters",
                                     })
        self.callback_meta(metric_name='loss', metric_namespace='train', metric_meta=metric_meta)
        self.callback_metric(metric_name='loss',
                             metric_namespace='train',
                             metric_data=[Metric(iter_num, loss)])

    def compute_mu(self, data_instances, coef_, intercept_=0, exposure=None):
        if exposure is None:
            mu = data_instances.mapValues(
                lambda v: np.exp(np.dot(v.features, coef_) + intercept_))
        else:
            mu = data_instances.join(exposure,
                                     lambda v, ei: np.exp(np.dot(v.features, coef_) + intercept_) / ei)
        return mu

    def fit(self, data_instance):
        pass

    def _get_meta(self):
        meta_protobuf_obj = poisson_model_meta_pb2.PoissonModelMeta(
            penalty=self.model_param.penalty,
            eps=self.model_param.eps,
            alpha=self.alpha,
            optimizer=self.model_param.optimizer,
            party_weight=self.model_param.party_weight,
            batch_size=self.batch_size,
            learning_rate=self.model_param.learning_rate,
            max_iter=self.max_iter,
            converge_func=self.model_param.converge_func,
            re_encrypt_batches=self.re_encrypt_batches,
            fit_intercept=self.fit_intercept,
            exposure_index=self.exposure_index)
        return meta_protobuf_obj

    def _get_param(self):
        header = self.header
        LOGGER.debug("In get_param, header: {}".format(header))
        if header is None:
            param_protobuf_obj = poisson_model_param_pb2.PoissonModelParam()
            return param_protobuf_obj

        weight_dict = {}
        for idx, header_name in enumerate(header):
            coef_i = self.model_weights.coef_[idx]
            weight_dict[header_name] = coef_i
        intercept_ = self.model_weights.intercept_
        param_protobuf_obj = poisson_model_param_pb2.PoissonModelParam(iters=self.n_iter_,
                                                                 loss_history=self.loss_history,
                                                                 is_converged=self.is_converged,
                                                                 weight=weight_dict,
                                                                 intercept=intercept_,
                                                                 header=header)
        json_result = json_format.MessageToJson(param_protobuf_obj)
        LOGGER.debug("json_result: {}".format(json_result))
        return param_protobuf_obj

    def export_model(self):
        meta_obj = self._get_meta()
        param_obj = self._get_param()
        result = {
            self.model_meta_name: meta_obj,
            self.model_param_name: param_obj
        }
        return result

    def _load_model(self, model_dict):
        #LOGGER.debug("In load model, model_dict: {}".format(model_dict))
        result_obj = list(model_dict.get('model').values())[0].get(
            self.model_param_name)
        meta_obj = list(model_dict.get('model').values())[0].get(self.model_meta_name)
        fit_intercept = meta_obj.fit_intercept
        self.exposure_index = meta_obj.exposure_index

        self.header = list(result_obj.header)
        #LOGGER.debug("In load model, header: {}".format(self.header))
        # For poisson regression arbiter predict function
        if self.header is None:
            return

        feature_shape = len(self.header)
        tmp_vars = np.zeros(feature_shape)
        weight_dict = dict(result_obj.weight)
        self.intercept_ = result_obj.intercept

        for idx, header_name in enumerate(self.header):
            tmp_vars[idx] = weight_dict.get(header_name)

        if fit_intercept:
            tmp_vars = np.append(tmp_vars, result_obj.intercept)
        self.model_weights = PoissonRegressionWeights(l=tmp_vars, fit_intercept=fit_intercept)

    def _abnormal_detection(self, data_instances):
        """
        Make sure input data_instances is valid.
        """
        abnormal_detection.empty_table_detection(data_instances)
        abnormal_detection.empty_feature_detection(data_instances)

    def cross_validation(self, data_instances):
        kflod_obj = KFold()
        cv_param = self._get_cv_param()
        kflod_obj.run(cv_param, data_instances, self)
        LOGGER.debug("Finish kfold run")
        return data_instances

    def _get_cv_param(self):
        self.model_param.cv_param.role = self.role
        self.model_param.cv_param.mode = self.mode
        return self.model_param.cv_param

    def set_schema(self, data_instance, header=None):
        if header is None:
            self.schema["header"] = self.header
        else:
            self.schema["header"] = header
        data_instance.schema = self.schema
        return data_instance

    def init_schema(self, data_instance):
        if data_instance is None:
            return
        self.schema = data_instance.schema
        self.header = self.schema.get('header')