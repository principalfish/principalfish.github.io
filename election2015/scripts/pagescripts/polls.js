$( function() {
    $.tablesorter.addParser({
        id: "numberWithComma",
        is: function(s) {
            return /^[0-9]?[0-9,\.]*$/.test(s);
        },
        format: function(s) {
            return $.tablesorter.formatFloat(s.replace(/,/g, ''));
        },
        type: "numeric"
    });
});


function doStuff(data) {

    $("#polltable").remove()
    $("#tableinfo").append("<table id=\"polltable\" class=\"tablesorter\"><thead><tr id=\"polltableheader\">" +
          "<th class=\"tablesorter-header\" style =\"width:80px;\">Pollster</th>" +
          "<th class=\"tablesorter-header\" style =\"width:80px;\">Date</th>" +
          "<th class=\"tablesorter-header\" style =\"width:142px;\">Region/Seat</th>" +
          "<th class=\"tablesorter-header\" style =\"width:40px;\">Polled</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">Con</th>" +
          "<th class=\"tablesorter-header\" style =\"width:42px;\">Labour</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">Lib</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">UKIP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">Green</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">SNP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">Plaid</th>" +
          "<th class=\"tablesorter-header\" style =\"width:45px;\">Other</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">SDLP</th>" +
          "<th class=\"tablesorter-header\" style =\"width:37px;\">SF</th>" +
          "<th class=\"tablesorter-header\" style =\"width:38px;\">All</th>" +
          "<th class=\"tablesorter-header\" style =\"width:38px;\">DUP</th>" +
          " <th class=\"tablesorter-header\" style =\"width:38px;\">UU</th>" +
          "</tr>" +
          "<tr><td colspan=\"18\" style=\"height: 15px\"></td></tr>" +
          "</thead>" +
          " <tbody id=\"polltablebody\"></tbody>" +
          "</table>")

		$.each(data, function(i){

      if (data[i].type == "seat")
        total = 100

      else
        total = data[i].total

			if (i == data.length -1)
					null

			else
        if (data[i].pollster != "me" && data[i].pollster != "whoever")
  				$("#polltablebody").append("<tr onmouseover=\"background-color: black; color:white;\"><td  class=\"" + data[i].pollster +
          "\" style=\"text-align: left; width: 102px;\">" + pollsters[data[i].pollster] +
          "</td><td style=\"width:102px;\">" + data[i].date +
          "</td><td style=\"text-align:left; width:164px;\">" + data[i].region +
          "</td><td style=\"width:62px;\">" + data[i].total +
          "</td><td style=\"width:59px;\">" + (100 * data[i].conservative/total).toFixed(0) +
          "</td><td style=\"width:64px;\">" + (100 * data[i].labour/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].libdems/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].ukip/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].green/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].snp/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].plaidcymru/total).toFixed(0)+
          "</td><td style=\"width:67px;\">" + (100 * data[i].other/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].sdlp/total).toFixed(0) +
          "</td><td style=\"width:59px;\">" + (100 * data[i].sinnfein/total).toFixed(0) +
          "</td><td style=\"width:60px;\">" + (100 * data[i].alliance/total).toFixed(0) +
          "</td><td style=\"width:60px;\">" + (100 * data[i].dup/total).toFixed(0) +
          "</td><td style=\"width:60px;\">" + (100 * data[i].uu/total).toFixed(0) +
          "</td></tr>")
		})


		$("#polltable").tablesorter({
      dateFormat: "ddmmyyyy",

      sortInitialOrder: "asc",
      headers: {
        1: { sorter: "shortDate"},
        3: { sortInitialOrder: 'desc' },
        4: { sortInitialOrder: 'desc' },
        5: { sortInitialOrder: 'desc' },
        6: { sortInitialOrder: 'desc' },
        7: { sortInitialOrder: 'desc' },
        8: { sortInitialOrder: 'desc' },
        9: { sortInitialOrder: 'desc' },
        10: { sortInitialOrder: 'desc' },
        11: { sortInitialOrder: 'desc' },
        12: { sortInitialOrder: 'desc' },
        13: { sortInitialOrder: 'desc' },
        14: { sortInitialOrder: 'desc' },
        15: { sortInitialOrder: 'desc' },
        16: { sortInitialOrder: 'desc' },

				17: {
					sorter: false
				}

    },
			sortList:[[1,1]]

		});
};



function parseData(url, callBack) {
	Papa.parse(url, {
		download: true,
		header: true,
		dynamicTyping: true,
		complete: function(results) {
			callBack(results.data);
		}
	});
}
parseData("/election2015/data/polls.csv", doStuff);
