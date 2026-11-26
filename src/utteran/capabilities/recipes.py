"""The core-owned list of implemented recipes; never a backend/route product."""

from utteran_contracts.capabilities import CapabilityRecipe, RecipeCatalog, RequestedRoute
from utteran_contracts.engines import get_engine


def _recipe(
    recipe_id: str,
    backend: str,
    family: str,
    variant: str | None,
    encoder: str,
    decoder: str,
    devices: tuple[str, ...],
    compute: tuple[str, ...],
    extras: tuple[str, ...],
) -> CapabilityRecipe:
    # Engine-level facts come from the one declaration table; a recipe only adds the
    # route, devices and extras of one execution profile.
    engine = get_engine(backend)
    if engine.model_family != family or (variant is not None) != bool(engine.native_variants):
        raise ValueError("recipe_engine_mismatch")
    return CapabilityRecipe.model_validate(
        dict(
            schema_version=1,
            id=recipe_id,
            revision=1,
            backend=backend,
            model_family=family,
            requested_route=RequestedRoute.model_validate(
                dict(
                    native_variant=variant,
                    encoder=encoder,
                    decoder=decoder,
                )
            ),
            devices=devices,
            compute_types=compute,
            platforms=("Windows",)
            if variant in {"vulkan", "openvino", "openvino_vulkan"}
            else ("Windows", "Linux"),
            required_extras=extras,
            native_required=variant is not None,
            worker_supported=recipe_id in engine.worker.recipes,
            word_timestamps="always" in engine.word_timestamp_modes,
            non_space_diarization=engine.non_space_word_timestamps,
        )
    )


RECIPES = (
    _recipe(
        "fw-cpu",
        "faster-whisper",
        "ctranslate2",
        None,
        "cpu",
        "cpu",
        ("cpu",),
        ("int8", "int8_float32", "float32"),
        (),
    ),
    _recipe(
        "fw-cuda",
        "faster-whisper",
        "ctranslate2",
        None,
        "cuda",
        "cuda",
        ("cuda",),
        ("int8", "int8_float32", "int8_float16", "float16", "float32"),
        ("cuda",),
    ),
    _recipe(
        "wc-cpu", "whisper-cpp", "ggml", "cpu", "cpu", "cpu", ("cpu",), ("ggml",), ("whisper-cpp",)
    ),
    _recipe(
        "wc-vulkan",
        "whisper-cpp",
        "ggml",
        "vulkan",
        "vulkan",
        "vulkan",
        ("gpu",),
        ("ggml",),
        ("whisper-cpp",),
    ),
    _recipe(
        "wc-ov",
        "whisper-cpp",
        "ggml",
        "openvino",
        "openvino-gpu",
        "cpu",
        ("gpu",),
        ("ggml",),
        ("whisper-cpp", "openvino"),
    ),
    _recipe(
        "wc-ov-vk",
        "whisper-cpp",
        "ggml",
        "openvino_vulkan",
        "openvino-gpu",
        "vulkan",
        ("gpu",),
        ("ggml",),
        ("whisper-cpp", "openvino"),
    ),
    _recipe(
        "genai-ov",
        "openvino-genai",
        "openvino-genai",
        None,
        "selected-device",
        "selected-device",
        ("cpu", "gpu", "npu"),
        ("int8",),
        ("openvino-genai",),
    ),
)


def recipe_catalog() -> RecipeCatalog:
    return RecipeCatalog(schema_version=1, recipes=RECIPES)


def get_recipe(recipe_id: str) -> CapabilityRecipe:
    for recipe in RECIPES:
        if recipe.id == recipe_id:
            return recipe
    raise ValueError("unsupported_recipe")
