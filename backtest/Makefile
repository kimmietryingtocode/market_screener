CXX ?= c++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS ?= -Iinclude
BUILD_DIR := build

.PHONY: all test clean

all: $(BUILD_DIR)/portfolio_backtest

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/portfolio_backtest: src/backtest.cpp src/main.cpp include/backtest.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/backtest.cpp src/main.cpp -o $@

$(BUILD_DIR)/test_backtest: src/backtest.cpp tests/test_backtest.cpp include/backtest.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/backtest.cpp tests/test_backtest.cpp -o $@

test: $(BUILD_DIR)/test_backtest
	$(BUILD_DIR)/test_backtest

clean:
	rm -rf $(BUILD_DIR)
