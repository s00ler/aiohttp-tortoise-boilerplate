import datetime
import json

from aiohttp import web, hdrs
from marshmallow import Schema
from multidict import MultiDict

from app.exceptions import APIException, BaseAPIException, NotFound, ProcessingError
from app.models import Model


class BaseView(web.View):
    serializer = Schema()
    response_serializer = Schema()

    async def pre_process_request(self):
        if self.request._method in {hdrs.METH_POST, hdrs.METH_PUT, hdrs.METH_PATCH, hdrs.METH_DELETE}:
            data_raw = await self.request.text()
            if data_raw:
                try:
                    data = json.loads(data_raw)
                except json.JSONDecodeError:
                    raise ProcessingError('Data parsing error, expected json')
            else:
                data = {}
        else:
            data = self.request.query
        result = self.serializer.load(data)
        if result.errors:
            raise APIException(
                details={
                    'fieldErrors': result.errors,
                    'details': 'Validation failed'
                },
                status_code=400
            )
        self.validated_data = result.data

    async def _iter(self):
        if self.request.method not in hdrs.METH_ALL:
            self._raise_allowed_methods()
        method = getattr(self, self.request.method.lower(), None)
        if method is None:
            self._raise_allowed_methods()
        try:
            await self.pre_process_request()
            resp = await method()
        except BaseAPIException as e:
            resp = e.response
        return resp


class ViewSet(BaseView):
    serializers_map = {}
    default_serializer = Schema()
    default_response_serializer = Schema()
    response_serializer_map = {}

    @property
    def serializer(self):
        serializer = self.serializers_map.get(self.request.method, self.default_serializer)
        return serializer

    @property
    def response_serializer(self):
        serializer = self.response_serializer_map.get(self.request.method, self.default_response_serializer)
        return serializer


class PaginateMixin:
    page_size = 10

    def paginate_result(self, result, count):
        query_params = MultiDict(self.request.query)
        page = int(query_params.get('page', 1))
        page_size = int(query_params.get('page_size', self.page_size))

        response = {
            'count': count,
            'next': None,
            'previous': None,
            'result': result,
        }

        if page > 1:
            previous_page_params = query_params
            if (page_size * (page - 1)) > count and count:
                previous_page_params['page'] = int(count / page_size) + 1
            else:
                previous_page_params['page'] = page - 1
            response['previous'] = self.get_request_url(previous_page_params)

        if (page * page_size) < count:
            next_page_params = query_params
            next_page_params['page'] = page + 1
            response['next'] = self.get_request_url(next_page_params)

        return response

    def get_request_url(self, params):
        for key, value in params.items():
            if isinstance(value, bool):
                params[key] = 'true' if value else 'false'
            elif isinstance(value, datetime.datetime):
                params[key] = value.isoformat()
        uri = self.request.match_info.route.resource.url_for().with_query(params)
        return f'{self.request.scheme}://{self.request.host}{uri}'


class GenericListView(ViewSet, PaginateMixin):
    model = Model

    async def post(self):
        instance = self.model(self.validated_data)
        await instance.fetch_related_models(self.request.app['db'])
        await instance.save(self.request.app['db'])
        response_data, _ = self.response_serializer.dump(instance)
        return web.json_response(response_data)

    async def get(self):
        instance_list = await self.model.get_list(self.request.app['db'], **self.validated_data)
        instance_count = await self.model.get_count(self.request.app['db'], **self.validated_data)
        serialized_response, _ = self.response_serializer.dump(instance_list)
        paginated_response = self.paginate_result(serialized_response, instance_count)
        return web.json_response(paginated_response)


class GenericDetailView(ViewSet):
    model = Model

    async def get(self):
        object_id = self.request.match_info['id']
        instance = await self.model.get_by_id(self.request.app['db'], object_id)
        if not instance:
            raise NotFound()
        response_data, _ = self.response_serializer.dump(instance)
        return web.json_response(response_data)

    async def patch(self):
        object_id = self.request.match_info['id']
        instance = await self.model.get_by_id(self.request.app['db'], object_id)
        if not instance:
            raise NotFound()
        for field, value in self.validated_data.items():
            setattr(instance, field, value)
        await instance.save(self.request.app['db'])
        response_data, _ = self.response_serializer.dump(instance)
        return web.json_response(response_data)
