voteTotals = {
  display : function(seatlist){
    voteTotals.sortState = {
        "name" : "asc",
        "region" : "asc",
        "leave" : "desc",
        "remain" : "desc"
    };

    voteTotals.reorder("leave", "desc");
  },

  activeSort : "leave",

  sortState : {},

  tableSort: function(parameter){
    parameter = parameter.slice(13);

    //remove from old table header
    $("#seatlist-sort" + voteTotals.activeSort).removeClass("sort-active")
    voteTotals.activeSort = parameter;

    $("#seatlist-sort" + voteTotals.activeSort).addClass("sort-active");

    voteTotals.reorder(parameter, voteTotals.sortState[parameter]);

    if (voteTotals.sortState[parameter] == "desc"){
      voteTotals.sortState[parameter] = "asc";
    } else if (voteTotals.sortState[parameter] == "asc"){
      voteTotals.sortState[parameter] = "desc";
    }
  },

  reorder : function(parameter, sorttype){
    var sortArray = filters.filteredList.map(function(data, ind){
      return {ind:ind, data:data}
    });

    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return -1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return 1;
        }
        return a.ind - b.ind;
      })
    }
    if (sorttype == "desc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return 1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return -1;
        }
        return a.ind - b.ind;
      })
    }

    filters.filteredList = sortArray.map(function(val){
      return val.data;
    });

    voteTotals.extendedList();
  },

  seatTotals: function(){
      $("#brexitvotes-seatnumbers").empty();
      var totalSeats = filters.filteredList.length;
      var remainSeats = 0;
      var leaveSeats = 0;

      $.each(filters.filteredList, function(seat, data){
        if (data.current == "leave"){
          leaveSeats += 1;
        } else {
          remainSeats += 1;
        }

      });

      var toAdd = "<p style='font-weight: bold'>Total : " + totalSeats + " &nbsp;Remain " +
      "<span class='party-flair remain'></span>"
      + ": " + remainSeats + "&nbsp; Leave " + "<span class='party-flair leave'></span>" + ": " + leaveSeats + "</p>"


      $("#brexitvotes-seatnumbers").append(toAdd);

  },

  extendedList : function(){
    voteTotals.seatTotals();

    $("#brexitvotes-data").empty();

    $.each(filters.filteredList, function(i, seat){

      var seatName = seat.name;

      var party = "<div style='margin-left: 10px' class='party-flair " + seat.current + "'></div>";
      var region =  "<div style='margin-left: 10px'>" + regionlist[seat.region] + "</div>";
      var winner2015 = "<div style='margin-left: 10px' class='party-flair " + seat.winner2015 + "'></div>"

      var leave = "<div style='margin-left: 10px'>" + seat.leave.toFixed(2) + "</div>"
      var remain = "<div style='margin-left: 10px'>" + seat.remain.toFixed(2) + "</div>"
      var divider = "</div><div>";

      var toAdd = "<div><div class='extended-seat' onclick='mapAttr.clicked(currentMap.seatData[\""
      + seat.name + "\"].mapSelect)'>" + seatName + divider + region + divider + party + divider + winner2015 + divider + leave + divider
      + remain + "</div></div>";

      $("#brexitvotes-data").append(toAdd);

    });
  }

};
