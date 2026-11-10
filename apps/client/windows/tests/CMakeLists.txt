# I-032 stage 1: unit tests for the Windows config factory. No third-party test framework is
# fetched (no new dependency); the tests use a tiny assertion harness.
cmake_minimum_required(VERSION 3.14)
project(detour_config_tests LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(RUNNER ${CMAKE_CURRENT_SOURCE_DIR}/../runner)

add_library(detour_config STATIC
  ${RUNNER}/domain_rule.cpp ${RUNNER}/profile.cpp ${RUNNER}/config_factory.cpp)
target_include_directories(detour_config PUBLIC ${RUNNER})
target_link_libraries(detour_config PUBLIC normaliz)
if(MSVC)
  target_compile_options(detour_config PRIVATE /W4 /WX /utf-8 /permissive-)
endif()

add_executable(config_factory_tests config_factory_tests.cpp)
target_link_libraries(config_factory_tests PRIVATE detour_config)
if(MSVC)
  target_compile_options(config_factory_tests PRIVATE /W4 /WX /utf-8 /permissive-)
endif()

# Native adapter (no Flutter types): CNG, DPAPI, engine process, controller.
add_library(detour_adapter STATIC
  ${RUNNER}/key_confirmation.cpp ${RUNNER}/secure_vault.cpp ${RUNNER}/engine_process.cpp
  ${RUNNER}/tunnel_controller.cpp ${RUNNER}/system_hooks.cpp)
target_include_directories(detour_adapter PUBLIC ${RUNNER})
target_link_libraries(detour_adapter PUBLIC detour_config bcrypt crypt32 shell32 ole32 uuid comdlg32 ws2_32)
if(MSVC)
  target_compile_options(detour_adapter PRIVATE /W4 /WX /utf-8 /permissive-)
endif()

# TEST DOUBLE for the engine; proves nothing about a tunnel. Never shipped.
add_executable(fake_engine fake_engine.cpp)
add_executable(adapter_tests adapter_tests.cpp)
add_dependencies(adapter_tests fake_engine)
target_link_libraries(adapter_tests PRIVATE detour_adapter)
if(MSVC)
  target_compile_options(adapter_tests PRIVATE /W4 /WX /utf-8 /permissive-)
  target_compile_options(fake_engine PRIVATE /W4 /WX)
endif()

add_executable(detour_config_tool ${RUNNER}/config_tool.cpp)
target_link_libraries(detour_config_tool PRIVATE detour_config)
if(MSVC)
  target_compile_options(detour_config_tool PRIVATE /W4 /WX /utf-8 /permissive-)
endif()

enable_testing()
add_test(NAME config_factory_tests COMMAND config_factory_tests)
add_test(NAME adapter_tests COMMAND adapter_tests)
