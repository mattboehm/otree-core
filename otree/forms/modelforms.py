import collections
import re

from django.core.exceptions import FieldError
from django.db import models
from django.template import loader
from django.template import Context
from django.template import RequestContext
from django.template import Variable
from django.template import VariableDoesNotExist
from django.utils import six

import otree.forms


FORM_FIELD_MARKER_ATTRIBUTE = 'is_form_field_marker'


class FormDefinitionError(Exception):
    """Raised when reading the form definition from a template fails."""

    def __init__(self, *args, **kwargs):
        self.code = kwargs.pop('code')
        self.node = kwargs.pop('node', None)
        super(FormDefinitionError, self).__init__(*args, **kwargs)


class NoFormDefinitionFound(Exception):
    """Raised when there is no {% formfield %} found in a template."""


class TemplateFormDefinition(object):
    """
    Helper to extract form definitions out of the template.

    Parses the template and traverses the template nodes to find all
    occurrences of {% formfield %} tags. Then the first argument for those tags
    are checked if they are present in the context that will be used to render
    the template.

    Form there the actual model instance can be extract and all the fields are
    found.

    Given this template:

        {% load otree_tags %}

        {% formfield player.name %}
        {% formfield player.age %}

    It will create a ModelForm class on the fly that is equal to this:

        class PlayerForm(forms.ModelForm):
            class Meta:
                model = Player
                fields = ('name', 'age',)
    """

    def __init__(self, template_names, context, request=None,
                 current_app=None):
        self.template_names = template_names
        self.context_data = context
        self._request = request
        self._current_app = current_app
        self._bootstrap()

    def _bootstrap(self):
        self.template = self.resolve_template(self.template_names)
        self.context = self.resolve_context(self.context_data)
        self.formfield_nodes = self.get_formfield_nodes()
        self.field_identifiers = self.get_field_identifiers()

    def resolve_template(self, template):
        "Accepts a template object, path-to-template or list of paths"
        if isinstance(template, (list, tuple)):
            return loader.select_template(template)
        elif isinstance(template, six.string_types):
            return loader.get_template(template)
        else:
            return template

    def error(self, message, code, node=None):
        assert node
        raise FormDefinitionError(
            'Error in {templatetag}: {message} Please refer to the otree '
            'documentation about the {{% formfield %}} template tag for '
            'further details.'.format(
                templatetag=node.get_tag_definition(),
                message=message),
            code=code,
            node=node)

    def resolve_context(self, context):
        """Converts context data into a full ``Context`` object
        (assuming it isn't already a ``Context`` object). It might return a
        ``RequestContext`` if request was passed into ``__init__``.
        """
        if isinstance(context, Context):
            return context
        if self._request is not None:
            return RequestContext(self._request, context,
                                  current_app=self._current_app)
        return Context(context)

    def get_formfield_nodes(self):
        formfield_nodes = []
        remaining_nodes = collections.deque(self.template.nodelist)

        while remaining_nodes:
            node = remaining_nodes.popleft()
            if getattr(node, FORM_FIELD_MARKER_ATTRIBUTE, False):
                formfield_nodes.append(node)
            if hasattr(node, 'child_nodelists'):
                for nodelist_attr in node.child_nodelists:
                    # There might be nodelist attributes that are in
                    # `child_nodelists` but not defined on the nodes. So we
                    # default to empty list.
                    nodelist = getattr(node, nodelist_attr, [])
                    remaining_nodes.extend(nodelist)
                # Support floppyform's {% form %} tag.
                if (
                        isinstance(getattr(node, 'options', None), dict) and
                        'nodelist' in node.options):
                    remaining_nodes.extend(node.options['nodelist'])

        if not formfield_nodes:
            if self.template.name:
                template_name = ': {0}'.format(self.template.name)
            else:
                template_name = ''
            raise NoFormDefinitionFound(
                'Unable to find any {{% formfield %}} template tags in the '
                'given template{template_name}. Please refer to the otree '
                'documentation to learn more about the limitations defining a '
                'form in the template with {{% formfield %}}.'.format(
                    template_name=template_name))

        return formfield_nodes

    def get_field_identifiers(self):
        identifiers = []

        for node in self.formfield_nodes:
            identifiers.extend(node.get_identifiers())

        previous_instance_name = None
        for identifier in identifiers:
            if not identifier.is_valid():
                self.error(
                    'Please provide a field in the format: <model>.<field>, '
                    'e.g. {% formfield mymodel.some_value %} if you want to '
                    'reference the `some_value` field on the `mymodel` '
                    'object.',
                    code='invalid_variable_format',
                    node=identifier.node)
            # Validate that only one model variable is used.
            if previous_instance_name is not None:
                if previous_instance_name != identifier.get_instance_name():
                    self.error(
                        'You cannot use different model instances in the same '
                        'form. Earlier in the template there is already'
                        '`{earlier_variable}` in use.'.format(
                            earlier_variable=previous_instance_name,),
                        code='multiple_instances_found',
                        node=identifier.node)
            previous_instance_name = identifier.get_instance_name()

        return identifiers

    def get_identifier_by_field_name(self, field_name):
        for identifier in self.field_identifiers:
            if identifier.get_field_name() == field_name:
                return identifier
        raise ValueError("Unknown field name '{field_name}'.".format(
            field_name=field_name))

    def get_model_instance(self):
        identifier = self.field_identifiers[0]
        variable = Variable(identifier.get_instance_name())
        try:
            instance = variable.resolve(self.context)
        except VariableDoesNotExist:
            self.error(
                'Cannot find variable `{variable}` in template context. '
                'Make sure that `{instance_name}` is available in the '
                'top level context.'.format(
                    variable=identifier.variable,
                    instance_name=identifier.get_instance_name()),
                code='instance_not_found',
                node=identifier.node)

        # Make sure the variable is an actual model instance.
        if not isinstance(instance, models.Model):
            self.error(
                'The variable `{instance_name}` does not contain a model '
                'instance, but is of type `{type}`.'.format(
                    instance_name=identifier.get_instance_name(),
                    type=type(instance)),
                code='unexpected_type',
                node=identifier.node)
        return instance

    def get_model_class(self):
        return self.get_model_instance().__class__

    def get_form_fields(self):
        fields = []
        for identifier in self.field_identifiers:
            fields.append(identifier.get_field_name())
        return fields

    def get_base_form_class(self):
        return otree.forms.ModelForm

    def get_form_class(self):
        try:
            form_class = otree.forms.modelform_factory(
                self.get_model_class(),
                fields=self.get_form_fields(),
                form=self.get_base_form_class(),
                formfield_callback=otree.forms.formfield_callback)
        except FieldError as error:
            # Trying to extract the field name from the modelforms error
            # message. That is a peculiar method, but I don't see another way
            # to get to the actual name without duplicating the
            # field-validation logic from Django's ModelFormMetaclass.
            match = re.search('Unknown field\(s\) \(([^\)]+)\)',
                              unicode(error))
            field_name = match.groups()[0]
            identifier = self.get_identifier_by_field_name(field_name)
            instance = self.get_model_instance()
            self.error(
                'The model class `{model}` of variable `{variable}` does not '
                'contain a field called `{field_name}`.'.format(
                    model=instance.__class__,
                    variable=identifier.variable,
                    field_name=field_name),
                code='not_a_field',
                node=identifier.node)
        return form_class

    def get_form(self, *args, **kwargs):
        instance = self.get_model_instance()
        form_class = self.get_form_class()
        kwargs.setdefault('instance', instance)
        return form_class(*args, **kwargs)

    def apply_to_context(self, context, form):
        """Should be used by the view to put the form instance into the
        context. By encapsulating this behaviour we are free to change the
        variable name or other behaviour later."""
        context['form'] = form
        return context


def get_modelform_from_template(template, context, request=None,
                                current_app=None):
    helper = TemplateFormDefinition(template, context, request=request,
                                    current_app=current_app)
    return helper.get_form_class()