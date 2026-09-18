from __future__ import annotations

import ckan.plugins as plugins
from ckan.plugins import toolkit

from ckanext.providerharvest.logic import action, auth
from ckanext.providerharvest.harvesters.base_generic import GenericProviderHarvester


class ProviderHarvestPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IBlueprint)

    try:
        from ckanext.harvest.interfaces import IHarvester
        plugins.implements(IHarvester)
    except ImportError:  # pragma: no cover - ckanext-harvest not installed
        pass

    # -- IConfigurable ------------------------------------------------

    def configure(self, config):
        from ckanext.providerharvest.model.meta import init_tables
        init_tables()

    # -- IConfigurer --------------------------------------------------

    def update_config(self, config):
        toolkit.add_template_directory(config, "templates")

    # -- IActions -------------------------------------------------------

    def get_actions(self):
        return {
            "provider_source_create": action.provider_source_create,
            "provider_source_list_mine": action.provider_source_list_mine,
            "provider_source_list_pending": action.provider_source_list_pending,
            "provider_source_list_all": action.provider_source_list_all,
            "provider_source_test_connection": action.provider_source_test_connection,
            "provider_source_fetch_host_key": action.provider_source_fetch_host_key,
            "provider_source_activate": action.provider_source_activate,
            "provider_source_reject": action.provider_source_reject,
            "provider_source_pause": action.provider_source_pause,
            "provider_source_resume": action.provider_source_resume,
        }

    # -- IAuthFunctions ---------------------------------------------------

    def get_auth_functions(self):
        return {
            "provider_source_create": auth.provider_source_create,
            "provider_source_show": auth.provider_source_show,
            "provider_source_update": auth.provider_source_update,
            "provider_source_delete": auth.provider_source_delete,
            "provider_source_list_mine": auth.provider_source_list_mine,
            "provider_source_list_pending": auth.provider_source_list_pending,
            "provider_source_list_all": auth.provider_source_list_all,
            "provider_source_test_connection": auth.provider_source_test_connection,
            "provider_source_fetch_host_key": auth.provider_source_fetch_host_key,
            "provider_source_activate": auth.provider_source_activate,
            "provider_source_reject": auth.provider_source_reject,
            "provider_source_pause": auth.provider_source_pause,
            "provider_source_resume": auth.provider_source_resume,
        }

    # -- IBlueprint ---------------------------------------------------

    def get_blueprint(self):
        from ckanext.providerharvest.blueprints.provider_ui import providerharvest
        return providerharvest

    # -- IHarvester -------------------------------------------------------
    # ckanext-harvest discovers harvesters via a plugin implementing
    # IHarvester directly, one class per plugin -- so this plugin *is*
    # the harvester, delegating to GenericProviderHarvester's methods.

    def info(self):
        return GenericProviderHarvester().info()

    def validate_config(self, config):
        return GenericProviderHarvester().validate_config(config)

    def gather_stage(self, harvest_job):
        return GenericProviderHarvester().gather_stage(harvest_job)

    def fetch_stage(self, harvest_object):
        return GenericProviderHarvester().fetch_stage(harvest_object)

    def import_stage(self, harvest_object):
        return GenericProviderHarvester().import_stage(harvest_object)
