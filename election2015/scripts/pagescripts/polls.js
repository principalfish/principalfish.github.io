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
          "<th class=\"tablesorter-header\">Pollster</th>" +
          "<th class=\"tablesorter-header\">Date</th>" +
          "<th class=\"tablesorter-header\">Region/Seat</th>" +
          "<th class=\"tablesorter-header\">Polled</th>" +
          "<th class=\"tablesorter-header\">Con</th>" +
          "<th class=\"tablesorter-header\">Labour</th>" +
          "<th class=\"tablesorter-header\">Lib</th>" +
          "<th class=\"tablesorter-header\">UKIP</th>" +
          "<th class=\"tablesorter-header\">Green</th>" +
          "<th class=\"tablesorter-header\">SNP</th>" +
          "<th class=\"tablesorter-header\">Plaid</th>" +
          "<th class=\"tablesorter-header\">Other</th>" +
          "<th class=\"tablesorter-header\">SDLP</th>" +
          "<th class=\"tablesorter-header\">SF</th>" +
          "<th class=\"tablesorter-header\">All</th>" +
          "<th class=\"tablesorter-header\">DUP</th>" +
          " <th class=\"tablesorter-header\">UU</th>" +
          "</tr>" +
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
          "\" style=\"text-align: left;\">" + pollsters[data[i].pollster] +
          "</td><td>" + data[i].date +
          "</td><td style=\"text-align:left;\">" + data[i].region +
          "</td><td>" + data[i].total +
          "</td><td>" + (100 * data[i].conservative/total).toFixed(0) +
          "</td><td>" + (100 * data[i].labour/total).toFixed(0) +
          "</td><td>" + (100 * data[i].libdems/total).toFixed(0) +
          "</td><td>" + (100 * data[i].ukip/total).toFixed(0) +
          "</td><td>" + (100 * data[i].green/total).toFixed(0) +
          "</td><td>" + (100 * data[i].snp/total).toFixed(0) +
          "</td><td>" + (100 * data[i].plaidcymru/total).toFixed(0)+
          "</td><td>" + (100 * data[i].other/total).toFixed(0) +
          "</td><td>" + (100 * data[i].sdlp/total).toFixed(0) +
          "</td><td>" + (100 * data[i].sinnfein/total).toFixed(0) +
          "</td><td>" + (100 * data[i].alliance/total).toFixed(0) +
          "</td><td>" + (100 * data[i].dup/total).toFixed(0) +
          "</td><td>" + (100 * data[i].uu/total).toFixed(0) +
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
			sortList:[[1,1]],
      widgets:["stickyHeaders", "filter"]

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
