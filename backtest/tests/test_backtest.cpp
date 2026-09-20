#include "backtest.hpp"

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::string synthetic_date(int observation) {
    const int year = 2020 + observation / 300;
    const int within_year = observation % 300;
    const int month = within_year / 25 + 1;
    const int day = within_year % 25 + 1;
    char buffer[11];
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d", year, month, day);
    return buffer;
}

void write_fixture(const std::filesystem::path& directory) {
    std::ofstream universe(directory / "universe.csv");
    universe << "ticker,company,sector,industry,sector_etf\n"
             << "AAA,Alpha,Technology,Software,XLK\n"
             << "BBB,Beta,Technology,Software,XLK\n"
             << "CCC,Gamma,Energy,Exploration,XLE\n"
             << "DDD,Delta,Energy,Exploration,XLE\n";

    std::ofstream prices(directory / "prices.csv");
    prices << "date,ticker,adjusted_close\n";
    for (int observation = 0; observation < 300; ++observation) {
        const std::string date = synthetic_date(observation);
        const auto emit = [&](const std::string& ticker, double daily_growth) {
            prices << date << ',' << ticker << ','
                   << 100.0 * std::pow(daily_growth, observation) << '\n';
        };
        emit("SPY", 1.0004);
        emit("XLK", 1.0010);
        emit("XLE", 0.9998);
        emit("AAA", 1.0020);
        emit("BBB", 1.0008);
        emit("CCC", 0.9997);
        emit("DDD", 0.9995);
    }
}

}  // namespace

int main() {
    try {
        require(
            backtest::Date::parse("2021-01-01").serial() -
                    backtest::Date::parse("2020-01-01").serial() ==
                366,
            "date serial calculation is invalid");
        const auto directory =
            std::filesystem::temp_directory_path() / "portfolio-backtest-cpp-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        write_fixture(directory);

        backtest::Config config;
        config.top_sectors = 1;
        config.top_industries_per_sector = 1;
        config.top_companies_per_industry = 1;
        config.rebalance_months = 3;
        config.trend_days = 100;
        config.maximum_position_weight = 0.25;
        config.maximum_sector_weight = 0.50;
        config.transaction_cost_bps = 10.0;

        auto prices = backtest::PriceTable::load_csv(
            (directory / "prices.csv").string(), config.benchmark);
        auto universe = backtest::load_universe_csv((directory / "universe.csv").string());
        const backtest::Result result =
            backtest::Engine(std::move(prices), std::move(universe), config).run();

        require(!result.selections.empty(), "expected at least one selection");
        for (const auto& selection : result.selections) {
            require(selection.ticker == "AAA", "walk-forward ranking selected wrong ticker");
            require(selection.target_weight <= 0.25 + 1e-12,
                    "maximum position weight was exceeded");
            require(selection.signal_date < selection.execution_date,
                    "selection must execute after its signal date");
        }
        require(!result.trades.empty(), "expected generated trades");
        require(result.total_transaction_costs > 0.0, "transaction costs were not applied");
        require(result.rebalance_count >= 1, "expected at least one rebalance");
        require(result.portfolio.ending_value > 0.0, "portfolio value must remain positive");
        require(result.portfolio.maximum_drawdown <= 0.0, "drawdown sign is invalid");

        const auto output = directory / "output";
        backtest::write_outputs(result, output.string());
        require(std::filesystem::is_regular_file(output / "summary.csv"),
                "summary output is missing");
        require(std::filesystem::is_regular_file(output / "equity_curve.csv"),
                "equity curve output is missing");
        require(std::filesystem::is_regular_file(output / "run_config.csv"),
                "run configuration output is missing");

        std::filesystem::remove_all(directory);
        std::cout << "all C++ backtest tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "test failure: " << error.what() << '\n';
        return 1;
    }
}
