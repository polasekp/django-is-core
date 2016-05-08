from __future__ import unicode_literals

import sys

from django.conf.urls import patterns as django_patterns
from django.core.urlresolvers import reverse
from django.shortcuts import get_object_or_404
from django.utils.translation import ugettext_lazy as _
from django.utils.datastructures import SortedDict
from django.http.response import Http404
from django.core.exceptions import ValidationError
from django.utils import six
from django.forms.models import _get_foreign_key
from django.utils.functional import cached_property

from pyston.utils import rfs

from is_core.actions import WebAction, ConfirmRESTAction
from is_core.generic_views.form_views import AddModelFormView, EditModelFormView, BulkChangeFormView
from is_core.generic_views.table_views import TableView
from is_core.rest.resource import RESTModelResource
from is_core.auth.main import PermissionsMixin, PermissionsUIMixin, PermissionsRESTMixin
from is_core.patterns import UIPattern, RESTPattern, DoubleRESTPattern
from is_core.utils import flatten_fieldsets, str_to_class
from is_core import config
from is_core.menu import LinkMenuItem
from is_core.loading import register_core
from is_core.rest.factory import modelrest_factory
from is_core.rest.datastructures import ModelRESTFieldset
from is_core.forms.models import SmartModelForm


class ISCoreBase(type):
    def __new__(cls, *args, **kwargs):
        name, _, attrs = args

        abstract = attrs.pop('abstract', False)
        super_new = super(ISCoreBase, cls).__new__
        new_class = super_new(cls, *args, **kwargs)
        model_module = sys.modules[new_class.__module__]
        app_label = model_module.__name__.split('.')[-2]

        if name != 'NewBase' and not abstract and new_class.register:
            register_core(app_label, new_class)
        return new_class


class ISCore(six.with_metaclass(ISCoreBase)):
    abstract = True
    register = True

    menu_url_name = None
    verbose_name = None
    verbose_name_plural = None
    menu_group = None

    def __init__(self, site_name, menu_parent_groups):
        self.site_name = site_name
        self.menu_parent_groups = menu_parent_groups

    def init_request(self, request):
        pass

    def init_ui_request(self, request):
        self.init_request(request)

    def init_rest_request(self, request):
        self.init_request(request)

    def get_urlpatterns(self, patterns):
        urls = []
        for pattern in patterns.values():
            url = pattern.get_url()
            if url:
                urls.append(url)
        urlpatterns = django_patterns('', *urls)
        return urlpatterns

    def get_urls(self):
        return ()

    def get_show_in_menu(self, request):
        return self.show_in_menu

    def get_views(self):
        return {}

    def menu_url(self, request):
        return reverse(('%(site_name)s:' + self.menu_url_name) % {'site_name': self.site_name})

    def get_menu_groups(self):
        menu_groups = list(self.menu_parent_groups)
        if self.menu_group:
            menu_groups += (self.menu_group,)
        return menu_groups

    def get_url_prefix(self):
        return '/'.join(self.get_menu_groups())

    def get_menu_group_pattern_name(self):
        return '-'.join(self.get_menu_groups())

    def get_menu_item(self, request, active_group):
        pass


class ModelISCore(PermissionsMixin, ISCore):
    abstract = True

    list_actions = ()

    # form params
    form_fields = None
    form_inline_views = ()
    form_exclude = ()
    form_class = SmartModelForm
    form_edit_class = None
    form_add_class = None

    ordering = None
    bulk_change_url_name = None

    field_labels = {}

    def get_field_labels(self, request):
        return self.field_labels

    def get_view_classes(self):
        view_classes = super(ModelISCore, self).get_view_classes()
        if self.is_bulk_change_enabled():
            view_classes[self.get_bulk_change_url_name()] = (r'/bulk-change/?$', BulkChangeFormView)
        return view_classes

    def get_form_fields(self, request, obj=None):
        return self.form_fields

    def get_form_edit_class(self, request, obj=None):
        return self.form_edit_class or self.get_form_class(request, obj)

    def get_form_add_class(self, request, obj=None):
        return self.form_add_class or self.get_form_class(request, obj)

    def get_form_class(self, request, obj=None):
        return self.form_class

    def get_form_exclude(self, request, obj=None):
        return self.form_exclude

    def pre_save_model(self, request, obj, form, change):
        pass

    def save_model(self, request, obj, form, change):
        obj.save()

    def post_save_model(self, request, obj, form, change):
        pass

    def pre_delete_model(self, request, obj):
        pass

    def delete_model(self, request, obj):
        obj.delete()

    def post_delete_model(self, request, obj):
        pass

    def verbose_name(self):
        return self.model._meta.verbose_name
    verbose_name = property(verbose_name)

    def verbose_name_plural(self):
        return self.model._meta.verbose_name_plural
    verbose_name_plural = property(verbose_name_plural)

    def menu_group(self):
        return str(self.model._meta.module_name)
    menu_group = property(menu_group)

    # TODO: remove this function
    def get_obj(self, request, **filters):
        try:
            return get_object_or_404(self.get_queryset(request), **filters)
        except (ValidationError, ValueError):
            raise Http404

    def get_ordering(self):
        return self.model._meta.ordering or ('pk',)

    def get_queryset(self, request):
        return self.model._default_manager.get_queryset().order_by(*self.get_ordering())

    def preload_queryset(self, request, qs):
        return qs

    def get_list_actions(self, request, obj):
        return list(self.list_actions)

    def get_default_action(self, request, obj):
        return None

    def is_bulk_change_enabled(self):
        return self.get_bulk_change_url_name() is not None

    def get_bulk_change_url_name(self):
        return self.bulk_change_url_name


class UIISCore(PermissionsUIMixin, ISCore):
    abstract = True

    api_url_name = None
    show_in_menu = True
    menu_url_name = None
    view_classes = SortedDict()
    default_ui_pattern_class = UIPattern

    def get_view_classes(self):
        return self.view_classes.copy()

    @cached_property
    def ui_patterns(self):
        return self.get_ui_patterns()

    def get_ui_patterns(self):
        ui_patterns = SortedDict()
        for name, view_vals in self.get_view_classes().items():
            if len(view_vals) == 3:
                pattern, view, ViewPatternClass = view_vals
            else:
                pattern, view = view_vals
                ViewPatternClass = self.default_ui_pattern_class

            pattern_names = [name]
            group_pattern_name = self.get_menu_group_pattern_name()
            if group_pattern_name:
                pattern_names += [self.get_menu_group_pattern_name()]

            ui_patterns[name] = ViewPatternClass('-'.join(pattern_names), self.site_name, pattern, view, self)
        return ui_patterns

    def get_urls(self):
        return self.get_urlpatterns(self.ui_patterns)

    def get_show_in_menu(self, request):
        return self.has_ui_read_permission(request)

    def is_active_menu_item(self, request, active_group):
        return active_group == self.menu_group

    def get_menu_item(self, request, active_group):
        if self.get_show_in_menu(request):
            return LinkMenuItem(self.verbose_name_plural, self.menu_url(request),
                                self.menu_group, self.is_active_menu_item(request, active_group))


class RESTISCore(PermissionsRESTMixin, ISCore):

    rest_classes = SortedDict()
    default_rest_resource_pattern_class = RESTPattern
    abstract = True

    def get_rest_classes(self):
        return self.rest_classes.copy()

    @cached_property
    def resource_patterns(self):
        return self.get_resource_patterns()

    def get_resource_patterns(self):
        rest_patterns = SortedDict()
        for name, rest_vals in self.get_rest_classes().items():
            if len(rest_vals) == 3:
                pattern, rest, RESTPatternClass = rest_vals
            else:
                pattern, rest = rest_vals
                RESTPatternClass = self.default_rest_resource_pattern_class

            pattern_names = [name]
            group_pattern_name = self.get_menu_group_pattern_name()
            if group_pattern_name:
                pattern_names += [self.get_menu_group_pattern_name()]
            rest_patterns[name] = RESTPatternClass('-'.join(pattern_names), self.site_name, pattern, rest, self)
        return rest_patterns

    def get_urls(self):
        return self.get_urlpatterns(self.resource_patterns)


class UIRESTISCoreMixin(object):

    def get_urls(self):
        return self.get_urlpatterns(self.resource_patterns) + self.get_urlpatterns(self.ui_patterns)


class UIRESTISCore(UIRESTISCoreMixin, UIISCore, RESTISCore):
    pass


class HomeUIISCore(UIISCore):

    menu_url_name = 'index'
    verbose_name_plural = _('Home')
    menu_group = 'home'
    abstract = config.HOME_IS_CORE != 'is_core.main.HomeUIISCore'

    def get_view_classes(self):
        HomeView = str_to_class(config.HOME_VIEW)
        return SortedDict((
                        ('index', (r'^$', HomeView)),
                    ))

    def menu_url(self, request):
        return '/'

    def get_url_prefix(self):
        return ''


class UIModelISCore(ModelISCore, UIISCore):
    abstract = True

    view_classes = SortedDict((
                        ('add', (r'^/add/$', AddModelFormView)),
                        ('edit', (r'^/(?P<pk>[-\w]+)/$', EditModelFormView)),
                        ('list', (r'^/?$', TableView)),
                    ))

    # list view params
    list_display = ('_obj_name',)
    export_display = ()
    export_types = config.GRID_EXPORT
    default_list_filter = {}

    # add/edit view params
    form_fieldsets = None
    form_readonly_fields = ()

    ui_form_add_class = None
    ui_form_edit_class = None
    ui_form_field_labels = None
    ui_list_field_labels = None

    def __init__(self, site_name, menu_parent_groups):
        super(UIModelISCore, self).__init__(site_name, menu_parent_groups)

    def get_ui_form_add_class(self, request, obj=None):
        return self.ui_form_add_class or self.get_form_add_class(request, obj)

    def get_ui_form_edit_class(self, request, obj=None):
        return self.ui_form_edit_class or self.get_form_edit_class(request, obj)

    def get_form_fieldsets(self, request, obj=None):
        return self.form_fieldsets

    def get_form_readonly_fields(self, request, obj=None):
        return self.form_readonly_fields

    def get_ui_form_fields(self, request, obj=None):
        return self.get_form_fields(request, obj)

    def get_ui_form_exclude(self, request, obj=None):
        return self.get_form_exclude(request, obj)

    def get_urls(self):
        return self.get_urlpatterns(self.ui_patterns)

    def get_show_in_menu(self, request):
        return 'list' in self.view_classes and self.show_in_menu and self.has_ui_read_permission(request)

    def get_form_inline_views(self, request, obj=None):
        return self.form_inline_views

    def get_default_list_filter(self, request):
        return self.default_list_filter.copy()

    @property
    def menu_url_name(self):
        return 'list-%s' % self.get_menu_group_pattern_name()

    def get_list_display(self, request):
        return list(self.list_display)

    def get_export_display(self, request):
        return list(self.export_display) or self.get_list_display(request)

    def get_export_types(self, request):
        return self.export_types

    def get_api_url_name(self):
        return self.api_url_name

    def get_add_url(self, request):
        if 'add' in self.ui_patterns:
            return self.ui_patterns.get('add').get_url_string(request)

    def get_api_url(self, request):
        return reverse(self.get_api_url_name())

    def get_ui_form_field_labels(self, request):
        return self.ui_form_field_labels if self.ui_form_field_labels is not None else self.get_field_labels(request)

    def get_ui_list_field_labels(self, request):
        return self.ui_list_field_labels if self.ui_list_field_labels is not None else self.get_field_labels(request)


class RESTModelISCore(RESTISCore, ModelISCore):
    abstract = True

    # Allowed rest fields
    rest_extra_fields = None
    rest_detailed_fields = None
    rest_general_fields = None
    rest_guest_fields = None

    # Default rest fields for list, obj and guest
    rest_default_detailed_fields = ('id', '_rest_links', '_obj_name')
    rest_default_general_fields = ('id', '_rest_links', '_obj_name')
    rest_default_guest_fields = ()
    rest_default_extra_fields = ()

    rest_form_edit_class = None
    rest_form_add_class = None

    rest_allowed_methods = ('get', 'delete', 'post', 'put')
    rest_obj_class_names = ()

    rest_resource_class = RESTModelResource
    rest_form_field_labels = None

    def __init__(self, site_name, menu_parent_groups):
        super(RESTModelISCore, self).__init__(site_name, menu_parent_groups)

    def get_rest_form_field_labels(self, request):
        return (
            self.rest_form_field_labels if self.rest_form_field_labels is not None
            else self.get_field_labels(request)
        )

    def get_rest_form_add_class(self, request, obj=None):
        return self.rest_form_add_class or self.get_form_add_class(request, obj)

    def get_rest_form_edit_class(self, request, obj=None):
        return self.rest_form_edit_class or self.get_form_edit_class(request, obj)

    def get_rest_form_fields(self, request, obj=None):
        return self.get_form_fields(request, obj)

    def get_rest_form_exclude(self, request, obj=None):
        return self.get_form_exclude(request, obj)

    def get_rest_extra_fields(self, request, obj=None):
        if self.rest_extra_fields:
            return rfs(self.rest_extra_fields)
        else:
            return rfs(self.model._rest_meta.extra_fields).join(rfs(self.rest_default_extra_fields))

    def get_rest_general_fields(self, request, obj=None):
        if self.rest_general_fields:
            return rfs(self.rest_general_fields)
        else:
            return rfs(self.model._rest_meta.default_general_fields).join(rfs(self.rest_default_general_fields))

    def get_rest_detailed_fields(self, request, obj=None):
        if self.rest_detailed_fields:
            return rfs(self.rest_detailed_fields)
        else:
            return rfs(self.model._rest_meta.default_detailed_fields).join(rfs(self.rest_default_detailed_fields))

    def get_rest_guest_fields(self, request, obj=None):
        if self.rest_guest_fields:
            return rfs(self.rest_guest_fields)
        else:
            return rfs(self.model._rest_meta.guest_fields).join(rfs(self.rest_default_guest_fields))

        return rfs(self.rest_guest_fields)

    def get_rest_obj_class_names(self, request, obj):
        return list(self.rest_obj_class_names)

    def get_rest_class(self):
        return modelrest_factory(self.model, self.rest_resource_class)

    def get_resource_patterns(self):
        resource_patterns = super(RESTModelISCore, self).get_resource_patterns()
        resource_patterns.update(DoubleRESTPattern(
            self.get_rest_class(),
            self.default_rest_resource_pattern_class, self
        ).patterns)
        return resource_patterns

    def get_list_actions(self, request, obj):
        list_actions = super(RESTModelISCore, self).get_list_actions(request, obj)
        if self.has_delete_permission(request, obj):
            confirm_dialog = ConfirmRESTAction.ConfirmDialog(_('Do you really want to delete "%s"') % obj)
            list_actions.append(ConfirmRESTAction('api-resource-%s' % self.get_menu_group_pattern_name(),
                                                  _('Delete'), 'DELETE', confirm_dialog=confirm_dialog,
                                                  class_name='delete', success_text=_('Record "%s" was deleted') % obj))
        return list_actions

    def web_link_patterns(self, request):
        return self.ui_patterns.values()


class UIRESTModelISCore(UIRESTISCoreMixin, RESTModelISCore, UIModelISCore):
    abstract = True
    ui_rest_extra_fields = ('_web_links', '_rest_links', '_default_action', '_actions', '_class_names', '_obj_name')

    def get_rest_extra_fields(self, request, obj=None):
        fieldset = super(UIRESTModelISCore, self).get_rest_extra_fields(request, obj)
        fieldset.join(rfs(self.ui_rest_extra_fields))
        return fieldset.join(
            ModelRESTFieldset.create_from_flat_list(self.get_list_display(request), self.model)
        )

    def get_api_url_name(self):
        return self.api_url_name or '%s:api-%s' % (self.site_name, self.get_menu_group_pattern_name())

    def get_rest_form_fields(self, request, obj=None):
        return flatten_fieldsets(self.get_form_fieldsets(request, obj) or ()) or self.get_form_fields(request, obj)

    def get_rest_form_exclude(self, request, obj=None):
        return self.get_form_readonly_fields(request, obj) + self.get_form_exclude(request, obj)

    def get_list_actions(self, request, obj):
        list_actions = super(UIRESTModelISCore, self).get_list_actions(request, obj)
        return [WebAction('edit-%s' % self.get_menu_group_pattern_name(), _('Edit'), 'edit')] + list(list_actions)

    def get_default_action(self, request, obj):
        return 'edit-%s' % self.get_menu_group_pattern_name()


class ViaRESTModelISCore(RESTModelISCore):
    via_model = None
    fk_name = None
    abstract = True

    def get_form_exclude(self, request, obj=None):
        exclude = super(ViaRESTModelISCore, self).get_form_exclude(request, obj)
        if obj:
            fk = _get_foreign_key(self.via_model, self.model, fk_name=self.fk_name).name
            exclude = list(exclude)
            exclude.append(fk)
        return exclude

    def has_rest_read_permission(self, request, obj=None, via=None):
        if not via or via[-1].model != self.via_model:
            return False
        return super(ViaRESTModelISCore, self).has_rest_read_permission(request, obj, via)

    def has_rest_create_permission(self, request, obj=None, via=None):
        if not via or via[-1].model != self.via_model:
            return False
        return super(ViaRESTModelISCore, self).has_rest_create_permission(request, obj, via)

    def has_rest_update_permission(self, request, obj=None, via=None):
        if not via or via[-1].model != self.via_model:
            return False
        return super(ViaRESTModelISCore, self).has_rest_update_permission(request, obj, via)

    def has_rest_delete_permission(self, request, obj=None, via=None):
        if not via or via[-1].model != self.via_model:
            return False
        return super(ViaRESTModelISCore, self).has_rest_delete_permission(request, obj, via)
