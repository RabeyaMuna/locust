import os
from itertools import chain

from jinja2 import Environment as JinjaEnvironment
from jinja2 import FileSystemLoader

from . import stats
from .runners import STATE_STOPPED, STATE_STOPPING, MasterRunner
from .user.inspectuser import get_ratio
from .util.date import format_duration, format_utc_timestamp

PERCENTILES_FOR_HTML_REPORT = [0.50, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0]
# Prefer package resources for locating the built webui templates. If that fails, fall back to the
# original filesystem-relative path so behavior remains backwards compatible when running from source.
try:
    import importlib.resources as _importlib_resources

    try:
        with _importlib_resources.as_file(_importlib_resources.files("locust.webui").joinpath("dist")) as _dist_path:
            DEFAULT_BUILD_PATH = str(_dist_path)
    except Exception:
        DEFAULT_BUILD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "dist")
except Exception:
    DEFAULT_BUILD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "dist")


def process_html_filename(options) -> None:
    option_mapping = {
        "{u}": options.num_users,
        "{r}": options.spawn_rate,
        "{t}": options.run_time,
    }
    for option_term, option_value in option_mapping.items():
        if option_value is not None:
            options.html_file = options.html_file.replace(option_term, str(int(option_value)))


def render_template_from(file, build_path=DEFAULT_BUILD_PATH, **kwargs):
    # Use filesystem loader when a valid build_path exists. Otherwise try to load templates from
    # package resources (locust.webui/dist). Finally, fall back to the original behavior so that
    # TemplateNotFound continues to surface if nothing is found.
    loader = None
    if build_path and os.path.isdir(build_path):
        loader = FileSystemLoader(build_path)
    else:
        try:
            import importlib.resources as _importlib_resources

            _dist = _importlib_resources.files("locust.webui").joinpath("dist")
            with _importlib_resources.as_file(_dist) as _dist_path:
                if os.path.isdir(str(_dist_path)):
                    loader = FileSystemLoader(str(_dist_path))
        except Exception:
            loader = None

    env = JinjaEnvironment(loader=loader)
    if loader is not None:
        template = env.get_template(file)
        return template.render(**kwargs)

    # Fallback: try to read the template source from package resources and render from string
    try:
        import importlib.resources as _importlib_resources

        template_path = _importlib_resources.files("locust.webui").joinpath("dist").joinpath(file)
        with _importlib_resources.as_file(template_path) as tpath:
            with open(tpath, encoding="utf-8") as f:
                template_src = f.read()
        return env.from_string(template_src).render(**kwargs)
    except Exception:
        # As a last resort, recreate the original environment which will raise TemplateNotFound if missing
        env = JinjaEnvironment(loader=FileSystemLoader(build_path))
        template = env.get_template(file)
        return template.render(**kwargs)


def get_html_report(
    environment,
    show_download_link=True,
    theme="",
):
    request_stats = environment.runner.stats

    start_time = format_utc_timestamp(request_stats.start_time)

    if end_ts := request_stats.last_request_timestamp:
        end_time = format_utc_timestamp(end_ts)
    else:
        end_ts = request_stats.start_time
        end_time = start_time

    host = None
    if environment.host:
        host = environment.host
    elif environment.runner.user_classes:
        all_hosts = {l.host for l in environment.runner.user_classes}
        if len(all_hosts) == 1:
            host = list(all_hosts)[0]

    requests_statistics = list(chain(stats.sort_stats(request_stats.entries), [request_stats.total]))
    failures_statistics = stats.sort_stats(request_stats.errors)
    exceptions_statistics = [
        {**exc, "nodes": ", ".join(exc["nodes"])} for exc in environment.runner.exceptions.values()
    ]

    if request_stats.history and request_stats.history[-1]["time"] < end_time:
        stats.update_stats_history(environment.runner, end_time)
    history = request_stats.history

    is_distributed = isinstance(environment.runner, MasterRunner)
    user_spawned = (
        environment.runner.reported_user_classes_count if is_distributed else environment.runner.user_classes_count
    )

    if environment.runner.state in [STATE_STOPPED, STATE_STOPPING]:
        user_spawned = environment.runner.final_user_classes_count

    task_data = {
        "per_class": get_ratio(environment.user_classes, user_spawned, False),
        "total": get_ratio(environment.user_classes, user_spawned, True),
    }

    return render_template_from(
        "report.html",
        template_args={
            "is_report": True,
            "requests_statistics": [stat.to_dict() for stat in requests_statistics],
            "failures_statistics": [stat.to_dict() for stat in failures_statistics],
            "exceptions_statistics": [stat for stat in exceptions_statistics],
            "response_time_statistics": [
                {
                    "name": stat.name,
                    "method": stat.method or "",
                    **{
                        str(percentile): stat.get_response_time_percentile(percentile)
                        for percentile in PERCENTILES_FOR_HTML_REPORT
                    },
                }
                for stat in requests_statistics
            ],
            "start_time": start_time,
            "end_time": end_time,
            "duration": format_duration(request_stats.start_time, end_ts),
            "host": str(host),
            "history": history,
            "show_download_link": show_download_link,
            "locustfile": str(environment.locustfile),
            "tasks": task_data,
            "percentiles_to_chart": stats.PERCENTILES_TO_CHART,
            "profile": str(environment.profile) if environment.profile else None,
        },
        theme="dark" if theme == "dark" else "light",
    )
