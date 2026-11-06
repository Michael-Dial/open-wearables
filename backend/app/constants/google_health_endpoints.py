# Google Health API endpoint paths. {data_type} is the kebab-case data type;
# {project} is the GCP project number.
LIST_ENDPOINT = "/v4/users/me/dataTypes/{data_type}/dataPoints"
ROLLUP_ENDPOINT = "/v4/users/me/dataTypes/{data_type}/dataPoints:rollUp"
DAILY_ROLLUP_ENDPOINT = "/v4/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp"
RECONCILE_ENDPOINT = "/v4/users/me/dataTypes/{data_type}/dataPoints:reconcile"
IDENTITY_ENDPOINT = "/v4/users/me/identity"
# The account's paired hardware (googlehealth.settings.readonly, already requested).
PAIRED_DEVICES_ENDPOINT = "/v4/users/me/pairedDevices"
SUBSCRIBERS_ENDPOINT = "/v4/projects/{project}/subscribers"
