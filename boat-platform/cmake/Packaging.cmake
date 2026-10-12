# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

# IMPORTANT: this module must be included *last* in the top-level CMakeLists,
# after every add_subdirectory() for a fetched dependency.
#
# include(CPack) writes ${CMAKE_BINARY_DIR}/CPackConfig.cmake -- the TOP-LEVEL
# binary directory, shared by every subproject. gRPC's bundled c-ares sets
# CPACK_PACKAGE_NAME to its own PROJECT_NAME and then calls include(CPack)
# itself, so whichever include(CPack) runs last wins the file. This module used
# to be included at the top, before the dependencies, which meant c-ares won:
# `cpack` produced c-ares_1.34.5-1_amd64.deb, c-ares-1.34.5-1.src.rpm and an
# 8.6 GB boat-platform-0.1.0-Source.tar.gz of the whole working tree, and the
# v0.1.0 release shipped those instead of BoAt. The release job's
# `ls build/release/*.deb` check passed, because a .deb did exist -- just not
# ours. Verify by name, not by glob.

# Set explicitly rather than relying on PROJECT_*: a dependency configured
# earlier may have left CPACK_PACKAGE_VERSION_* pointing at its own version.
set(CPACK_PACKAGE_NAME "boat-platform")
set(CPACK_PACKAGE_VENDOR "boat-platform")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_VERSION_MAJOR "${PROJECT_VERSION_MAJOR}")
set(CPACK_PACKAGE_VERSION_MINOR "${PROJECT_VERSION_MINOR}")
set(CPACK_PACKAGE_VERSION_PATCH "${PROJECT_VERSION_PATCH}")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY
    "Deterministic automotive simulation and testing platform (SIL/HIL/CI)")
set(CPACK_PACKAGE_HOMEPAGE_URL "https://github.com/AlexTech-stack/BoAt")
set(CPACK_PACKAGE_CONTACT "Alexander Günther <alexander.guenther@tuta.io>")

# Pinned so the output filename is predictable and therefore checkable.
set(CPACK_PACKAGE_FILE_NAME
    "boat-platform-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")

set(CPACK_GENERATOR "TGZ;DEB;RPM")

# The gateway links its dependencies statically, so the unstripped binary is a
# few hundred MB of which most is symbol table.
set(CPACK_STRIP_FILES ON)

set(CPACK_DEBIAN_PACKAGE_MAINTAINER "Alexander Günther <alexander.guenther@tuta.io>")
# The floors are the portable-build baseline (Dockerfile.build: AlmaLinux 8,
# glibc 2.28, GCC 8's libstdc++), which packaging/check-portable-binary.sh
# enforces on the binary. An unversioned dependency let apt install the package
# on a system that then failed at load time. The RPM needs no equivalent:
# rpmbuild derives `Requires: libc.so.6(GLIBC_2.28)` etc. from the ELF itself.
set(CPACK_DEBIAN_PACKAGE_DEPENDS "libc6 (>= 2.28), libstdc++6 (>= 8), libacl1")
# CPack takes the Debian architecture from `dpkg --print-architecture` and falls
# back to i386 when dpkg is absent -- which it is in the AlmaLinux build image.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
  set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE "amd64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
  set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE "arm64")
endif()
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_RPM_PACKAGE_LICENSE "Apache-2.0")
set(CPACK_RPM_PACKAGE_GROUP "Applications/Engineering")
set(CPACK_RPM_FILE_NAME RPM-DEFAULT)
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_SOURCE_DIR}/LICENSE")

# `cpack --config CPackSourceConfig.cmake` only. Without these ignores a source
# package tars up build/ as well -- which is how 8.6 GB happened.
set(CPACK_SOURCE_GENERATOR "TGZ")
set(CPACK_SOURCE_PACKAGE_FILE_NAME "boat-platform-${PROJECT_VERSION}-src")
set(CPACK_SOURCE_IGNORE_FILES
    "/build/"
    "/\\\\.git/"
    "/\\\\.pytest_cache/"
    "__pycache__"
    "/spec/"
    "/traces/"
    "/reports/"
    "\\\\.db$"
    "\\\\.egg-info"
)

include(CPack)
