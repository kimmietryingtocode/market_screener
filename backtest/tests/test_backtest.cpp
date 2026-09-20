#include "backtest.hpp"

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

std::string fixture_date(int observation) {
    const int month = observation / 25 + 1;
    const int day = observation % 25 + 1;
    char value[11];
    std::snprintf(value, sizeof(value), "2023-%02d-%02d", month, day);
    return value;
}

template <typename Callback>
void require_throws(Callback callback, const std::string& message) {
    try {
        callback();
    } catch (const std::exception&) {
        return;
    }
    throw std::runtime_error(message);
}

}  // namespace

int main() {
    try {
        const auto directory =
            std::filesystem::temp_directory_path() / "target-schedule-backtest-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        std::ofstream prices(directory / "prices.csv");
        prices << "date,ticker,adjusted_close\n";
        for (int index = 0; index < 140; ++index) {
            prices << fixture_date(index) << ",SPY," << 100.0 + index * 0.1 << '\n';
            prices << fixture_date(index) << ",AAA," << 80.0 + index * 0.2 << '\n';
        }
        prices.close();
        std::ofstream targets(directory / "targets.csv");
        targets << "signal_at,execution_date,security_id,target_weight,provenance_id\n"
                << fixture_date(129) << "T18:00:00-05:00," << fixture_date(130)
                << ",AAA,1,buy\n"
                << fixture_date(134) << "T18:00:00-05:00," << fixture_date(135)
                << ",,0,cash\n";
        targets.close();

        backtest::Config config;
        config.start_date = backtest::Date::parse(fixture_date(128));
        config.end_date = backtest::Date::parse(fixture_date(139));
        const auto table = backtest::PriceTable::load_csv(
            (directory / "prices.csv").string(), "SPY");
        const auto schedule = backtest::load_target_schedule_csv(
            (directory / "targets.csv").string());
        const auto result = backtest::run_target_schedule(table, schedule, config);
        require(result.rebalance_count == 2, "expected buy and liquidation");
        require(result.account_ledger.size() == 12, "daily ledger is incomplete");
        require(result.account_ledger[2].cash >= -1e-6, "full investment produced negative cash");
        require(result.equity_curve[2].daily_return < 0.0,
                "entry cost was omitted from the first execution return");
        require(std::abs(result.portfolio.total_return -
                         (result.portfolio.ending_value / config.initial_capital - 1.0)) < 1e-12,
                "total return did not use starting capital");
        require(result.initial_waiting_sessions == 2, "initial wait was not disclosed");

        const auto output = directory / "output";
        backtest::write_outputs(result, output.string());
        require(std::filesystem::is_regular_file(output / "account_ledger.csv"),
                "account ledger output is missing");
        require(std::filesystem::is_regular_file(output / "holdings.csv"),
                "holdings output is missing");

        std::ofstream broken(directory / "broken.csv");
        broken << "date,ticker,adjusted_close\n";
        for (int index = 0; index < 140; ++index) {
            broken << fixture_date(index) << ",SPY," << 100.0 + index * 0.1 << '\n';
            if (index != 132) {
                broken << fixture_date(index) << ",AAA," << 80.0 + index * 0.2 << '\n';
            }
        }
        broken.close();
        const auto incomplete = backtest::PriceTable::load_csv(
            (directory / "broken.csv").string(), "SPY");
        require_throws(
            [&] { (void)backtest::run_target_schedule(incomplete, schedule, config); },
            "missing held-position price was silently substituted");

        std::filesystem::remove_all(directory);
        std::cout << "all C++ target-schedule tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "test failure: " << error.what() << '\n';
        return 1;
    }
}
